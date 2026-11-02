"""Exercise r2 prompt packing, local amendments and saved facts without HTTP."""
from copy import deepcopy
import json
from pathlib import Path
import re
import tempfile
import unittest

import yaml

from bookengine import q1, q1_plan
from bookengine.client import Client
from bookengine.config import Book
from bookengine.storage import load, save
from bookengine.text import number_paragraphs


class CaptureClient(Client):
    def __init__(self, out, bodies, replies=None):
        # Keep Client.stage's real parser/cache, bypass credentials and HTTP setup.
        self.out, self.bodies = out, iter(bodies)
        self.replies = dict(replies or {})
        self.prefix = 'PREFIX_SENTINEL'
        self.calls = []
        self.context_limit = 10000
        self.clip_context = True
        self.relaxed = True
        self.minimal_prose = True
        self.history_path = out / 'calls.json'

    def request(self, stage, prompt, cap, effort, attempt):
        entry = dict(seq=len(self.calls)+1, stage=stage, prompt=prompt,
                     status='ok', effort=effort, attempt=attempt)
        self.calls.append(entry)
        if stage in self.replies:
            value = self.replies[stage]
            if isinstance(value, Exception):
                entry['status'] = 'failed'
                raise value
            return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        if re.fullmatch(r'r2-ch\d+-draft', stage):
            return next(self.bodies)
        if re.fullmatch(r'r2-ch\d+-(?:finish|final-state)', stage):
            return json.dumps(dict(summary='阿宁在院里等她。', events=[], advances=[],
                state={'characters': {'阿宁': {'location': '院', 'present': True}}},
                scene_types=['daily'], type='日常', payoff={'type':'无兑现','fulfilled':False,'quote':''},
                hook_type=q1.PLAN_DATA['hook_types'][0], structure='日常喘息里藏冲突'), ensure_ascii=False)
        raise AssertionError('Unexpected request: ' + stage)


def body(dialogue_chars, total=200):
    dialogue = ('我陪你回家' * total)[:dialogue_chars]
    narration = ('她走到院门前停下' * total)[:total-dialogue_chars]
    return f'“{dialogue}。”\n\n{narration}。'


class MinimalBTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.out = Path(temporary.name)
        self.data = yaml.safe_load(q1.DATA.read_text(encoding='utf-8'))
        self.book = Book('suspense-mystery', 25, 200, 'USER_INPUT_SENTINEL')
        self.bible = dict(premise='BIBLE_SENTINEL', summary='BIBLE_SUMMARY_SENTINEL',
            world={'rules': ['PARAGRAPH_RULE_SENTINEL']},
            characters=[dict(name='阿宁', role='protagonist', voice='说话直接，先问再答。',
                             taboos=['TABOO_SENTINEL'], habits=['HABIT_SENTINEL'])])
        self.bible['canon'] = deepcopy(q1_plan.GROWTH['packs'][self.book.prompt_pack])
        self.bible['canon']['hook_types'] = list(q1.PLAN_DATA['hook_types'])
        self.threads = {'threads': [dict(id='line', kind='dark', beats=[
            dict(id='wait', volume=1, description='阿宁在院里等她，要求一起回家')])]}
        self.outline = dict(number=2, title='等候', scenes=[{'characters': ['阿宁']}],
                            structure='对峙谈判', summary='OUTLINE_SUMMARY_SENTINEL')
        self.previous = '她把门推开，阿宁就在院里。'
        save(self.out / 'chapter-001.body.md', self.previous)

    def run_chapter(self, client, state=None, rows=None, outline=None):
        return q1.chapter(client, self.book, self.bible, self.threads, {},
            {'number': 1, 'start': 1, 'end': 25}, self.outline if outline is None else outline,
            {} if state is None else state, [{'summary': 'HISTORY_SUMMARY_SENTINEL'}],
            [{'number': 1, 'summary': 'RECENT_SUMMARY_SENTINEL'}] if rows is None else rows,
            self.out, 'b', self.data)

    def extraction(self, **updates):
        result = dict(summary='阿宁在院里等她。', type='日常',
            hook_type=q1.PLAN_DATA['hook_types'][0], scene_types=['daily'],
            state={'characters': {'阿宁': {'location':'院', 'present':True}}},
            payoff={'type':'无兑现','fulfilled':False,'quote':''},
            gains=[], costs=[], solutions=[], advances=[], events=[])
        result.update(updates)
        return result

    def assert_saved(self, row, text, number=2):
        self.assertEqual((self.out / f'chapter-{number:03d}.body.md').read_text(), text)
        self.assertEqual(load(self.out / f'chapters/{number:03d}.json'), row)
        self.assertEqual(row['status'], 'complete')
        self.assertEqual(row['fact_source'], 'final_body')
        self.assertEqual(row['chars'], q1.chinese_count(text))

    def assert_calls(self, client, stages):
        self.assertEqual([c['stage'] for c in client.calls], stages)
        # Count every request except drafting, including any word/length/repair.
        outside = [c for c in client.calls if not c['stage'].endswith('-draft')]
        self.assertLessEqual(len(outside), 2)
        self.assertTrue(all(c['attempt'] == 1 for c in client.calls))
        self.assertTrue(all('PREFIX_SENTINEL' not in c['prompt'] for c in client.calls))
        self.assertEqual(client.prefix, 'PREFIX_SENTINEL')

    def test_writer_has_four_blocks_without_style_or_prefix_leak(self):
        for dialogue in (30, 60):
            with self.subTest(dialogue=dialogue), tempfile.TemporaryDirectory() as directory:
                self.out = Path(directory)
                save(self.out/'chapter-001.body.md', self.previous)
                client = CaptureClient(self.out, [body(dialogue)])
                row = self.run_chapter(client)
                prompt = client.calls[0]['prompt']
                self.assertEqual(re.findall(r'^## (.+)$', prompt, re.M),
                    ['本章章纲', '出场人物档案、口吻卡与当前状态', '上章结尾', '本章推进线'])
                self.assertIn('目标200汉字', prompt.split('## ', 1)[0])
                for required in ('只写两场戏', '说话直接，先问再答。', self.previous,
                                 '阿宁在院里等她，要求一起回家'):
                    self.assertIn(required, prompt)
                for forbidden in ('PREFIX_SENTINEL', 'BIBLE_SENTINEL', 'BIBLE_SUMMARY_SENTINEL',
                        'PARAGRAPH_RULE_SENTINEL', 'TABOO_SENTINEL', 'HABIT_SENTINEL',
                        'OUTLINE_SUMMARY_SENTINEL', 'HISTORY_SUMMARY_SENTINEL', 'RECENT_SUMMARY_SENTINEL',
                        'USER_INPUT_SENTINEL', '圣经', 'summary', 'taboos', 'habits',
                        '单句', '段落', '每段', '3—6句', '18—24段'):
                    self.assertNotIn(forbidden, prompt)
                self.assert_calls(client, ['r2-ch2-draft', 'r2-ch2-finish'])
                self.assertEqual(row['style']['dialogue_ratio'], dialogue/200)
                self.assertEqual(row['style']['single_sentence_ratio'], 1)
                self.assertFalse(row['dialogue_rewrite'])
                self.assertEqual(row['state']['characters']['阿宁']['location'], '院')
                self.assertEqual(row['summary'], '阿宁在院里等她。')
                self.assertTrue(row['within_band'])
                self.assert_saved(row, body(dialogue))

    def test_old_final_checkpoint_keeps_every_file_without_requests(self):
        row = dict(number=2, status='complete', fact_source='final_body',
                   writer='q1-r2', summary='旧摘要', state={'characters': {}})
        save(self.out/'chapters/002.json', row)
        save(self.out/'chapter-002.body.md', '旧正文只有短段。')
        save(self.out/'chapter-002.draft.md', '旧初稿。')
        def snapshot():
            return {p: (p.read_bytes(), p.stat().st_mtime_ns)
                    for p in self.out.rglob('*') if p.is_file()}
        before = snapshot()
        client = CaptureClient(self.out, [])
        self.assertEqual(self.run_chapter(client), row)
        self.assertEqual(client.calls, [])
        self.assertEqual(snapshot(), before)

    def test_context_keeps_dashboard_first_while_trimming_other_fields(self):
        hero = dict(self.bible['characters'][0], desire='主动欲望'*180, fear='具体恐惧'*180,
                    gender='女', address='阿宁')
        support = [dict(name=f'配角{i}', role='support', voice='说话直接', desire='各自目标') for i in range(3)]
        bible = dict(self.bible, characters=[*support, hero])
        dashboard = dict(strength=self.bible['canon']['ladder']['strength'][0], wealth='2元',
                         status=self.bible['canon']['ladder']['status'][0], stage='旧城', affiliation='无')
        state = dict(dashboard=dashboard, characters={c['name']: {'location':'长状态'*250} for c in [hero,*support]})
        chapter = dict(number=2, scenes=[{'characters':[c['name'] for c in support]}])
        before, dropped = deepcopy((bible,state,chapter)), []
        sections = dict(q1.context(bible,self.threads,{'number':1},chapter,state,[],[],'',dropped))
        cards = json.loads(sections['出场人物档案、口吻卡与当前状态'])['characters']
        self.assertEqual(cards[0]['name'], '阿宁')
        self.assertEqual(next(iter(cards[0])), 'dashboard')
        self.assertEqual(cards[0]['dashboard'], dashboard)
        self.assertLessEqual(len(q1.pack(cards[0]['dashboard'])), 200)
        self.assertEqual(dropped[:3], [c['name']+'.current' for c in support])
        self.assertLess(dropped.index('阿宁.desire'), dropped.index('阿宁.fear'))
        self.assertNotIn('阿宁.dashboard', dropped)
        self.assertEqual((cards[0]['voice'],cards[0]['gender'],cards[0]['address']), ('说话直接，先问再答。','女','阿宁'))
        self.assertEqual((bible,state,chapter), before)

    def test_invalid_extraction_returns_raw_once_without_recovery_chain(self):
        raw = dict(type='日常', state={'characters':{'阿宁':{'location':'未证实地点'}}}, summary='保留失败内容')
        client = CaptureClient(self.out, [], {'r2-ch2-finish':raw})
        state = {'custom':'keep'}
        value, failed, retries = q1.extract_state(client,'r2-ch2-finish',body(60),state,self.bible,self.outline,[])
        self.assertEqual((failed,retries), (True,0))
        self.assertEqual(value['summary'], raw['summary'])
        self.assertEqual(value['state'], raw['state'])
        self.assertEqual(load(self.out/'steps/r2-ch2-finish.json'), value)
        self.assertEqual(state, {'custom':'keep'})
        self.assert_calls(client, ['r2-ch2-finish'])

    def test_failed_finish_preserves_old_state_without_adopting_invalid_patch(self):
        old = dict(characters={'阿宁':{'location':'旧院','present':True}},
            dashboard=dict(strength=self.bible['canon']['ladder']['strength'][0]),
            continuity={'balances':{'寿命':{'amount':2,'unit':'天'}}}, gains=[{'chapter':1,'type':'旧得','content':'旧物'}])
        before = deepcopy(old)
        raw = dict(type='日常', state={'characters':{'阿宁':{'location':'未证实码头'}},
                                     'continuity':{'balances':{'寿命':{'amount':99,'unit':'天'}}}})
        client = CaptureClient(self.out, [body(60)], {'r2-ch2-finish':raw})
        row = self.run_chapter(client, state=old)
        self.assert_calls(client, ['r2-ch2-draft','r2-ch2-finish'])
        self.assertTrue(row['state_failed'])
        self.assertTrue(row['state_initial_failed'])
        self.assertEqual(row['state_retries'], 0)
        for key in old:
            self.assertEqual(row['state'][key], old[key])
        self.assertEqual(old, before)
        self.assert_saved(row, body(60))

    def test_patch_requires_actual_adoption_and_final_body_evidence(self):
        draft, addition = body(60), '她拿回旧钥匙，当场打开门。'
        kind = self.bible['canon']['payoff_menu'][0]
        for evidence in ('pid','quote','unfulfilled','forged','invalid_pid','rejected','unchanged'):
            with self.subTest(evidence=evidence), tempfile.TemporaryDirectory() as directory:
                self.out = Path(directory)
                patch_value = dict(after=1, text=addition)
                if evidence == 'rejected': patch_value['after'] = 0
                if evidence == 'unchanged': patch_value.update(text=draft.split('\n\n')[0],replace=True)
                first = self.extraction(patch=patch_value, state={'characters':{'阿宁':{'location':'初提地点'}}})
                payoff = dict(type=kind, fulfilled=True, pid=2) if evidence == 'pid' else dict(type=kind,fulfilled=True,quote=addition)
                if evidence == 'invalid_pid': payoff = dict(type=kind,fulfilled=True,pid=999)
                if evidence == 'forged': payoff['quote'] = '不存在于定稿的兑现'
                if evidence == 'unfulfilled': payoff = dict(type='无兑现',fulfilled=False,quote='')
                final = self.extraction(payoff=payoff, patch={'after':1,'text':'不得再次采用'},
                    edits=[{'id':1,'text':'不得再次润色'}])
                client = CaptureClient(self.out,[draft],{'r2-ch1-finish':first,'r2-ch1-final-state':final})
                row = self.run_chapter(client, rows=[], outline=dict(self.outline,number=1,payoff_type=kind))
                changed = evidence not in ('rejected','unchanged')
                self.assert_calls(client, ['r2-ch1-draft','r2-ch1-finish']+(['r2-ch1-final-state'] if changed else []))
                expected = draft.replace('\n\n','\n\n'+addition+'\n\n',1) if changed else draft
                self.assert_saved(row,expected,1)
                self.assertEqual((self.out/'chapter-001.r2.draft.md').read_text(),draft)
                self.assertEqual(row['outside_calls'], 1+int(changed))
                self.assertEqual(row['payoff_fulfilled'], evidence in ('pid','quote'))
                self.assertEqual(row['state_failed'], evidence in ('forged','invalid_pid'))
                if evidence in ('pid','quote'):
                    self.assertEqual(row['payoff']['quote'],addition)
                    self.assertIsNone(row['state']['pending_payoff'])
                else:
                    self.assertEqual(row['state']['pending_payoff'],kind)
                if changed:
                    self.assertIn(number_paragraphs(expected),client.calls[-1]['prompt'])
                    final_schema = client.calls[-1]['prompt'].split('只返回完整JSON，结构：')[-1]
                    for field in ('edits','paragraph_edits','patch','tail','previous_delta'):
                        self.assertNotIn('"'+field+'":',final_schema)
                    self.assertNotIn('不得再次采用',expected)
                    if row['state_failed']:
                        self.assertEqual(row['state'], {'pending_payoff':kind})
                    else:
                        self.assertEqual(row['state']['characters']['阿宁']['location'],'院')

    def test_sentence_edit_changes_only_named_hit_and_uses_final_state(self):
        draft = '她明白这不是胜利，而是新的开始。\n\n“我陪你回家。”\n\n她将钥匙收进衣袋。'
        replacement = '她将院门推开。'
        first = self.extraction(edits=[dict(id=1,text=replacement,reason='命中套话')],
            state={'continuity':{'balances':{'寿命':{'amount':99,'unit':'天'}}}})
        final = self.extraction(state={'continuity':{'balances':{'寿命':{'amount':1,'unit':'天'}}}},
            advances=[dict(thread='line',beat='wait',done=True,event='阿宁在院里等她')])
        old = {'continuity':{'balances':{'寿命':{'amount':2,'unit':'天'}}}}
        before = deepcopy(old)
        client = CaptureClient(self.out,[draft],{'r2-ch2-finish':first,'r2-ch2-final-state':final})
        row = self.run_chapter(client,state=old)
        self.assert_calls(client,['r2-ch2-draft','r2-ch2-finish','r2-ch2-final-state'])
        expected = replacement+'\n\n'+draft.split('\n\n',1)[1]
        self.assert_saved(row,expected)
        self.assertEqual(row['state']['continuity']['balances']['寿命'],{'amount':1,'unit':'天'})
        self.assertEqual(row['state']['ledger']['line']['done'],['wait'])
        self.assertEqual(row['state']['ledger']['line']['last'],2)
        self.assertEqual(old,before)

    def test_dialogue_and_length_are_local_finish_repairs_without_extra_calls(self):
        cases = [('dialogue',body(10),dict(patch=dict(after=1,replace=True,text=body(35).split('\n\n')[0]))),
                 ('long',body(60,340),dict(paragraph_edits=[dict(paragraph=2,text=body(60).split('\n\n')[1])])),
                 ('short',body(20,100),dict(paragraph_edits=[dict(paragraph=2,text=body(20).split('\n\n')[1])]))]
        for label,draft,proposal in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                self.out = Path(directory)
                save(self.out/'chapter-001.body.md',self.previous)
                client = CaptureClient(self.out,[draft],{'r2-ch2-finish':self.extraction(**proposal)})
                row = self.run_chapter(client)
                self.assert_calls(client,['r2-ch2-draft','r2-ch2-finish','r2-ch2-final-state'])
                final = (self.out/'chapter-002.body.md').read_text()
                self.assert_saved(row,final)
                self.assertTrue(row['within_band'])
                self.assertEqual(row['outside_calls'],2)
                self.assertTrue(row['length_correction'])
                self.assertFalse(row['dialogue_rewrite'])
                self.assertEqual((self.out/'chapter-002.r2.draft.md').read_text(),draft)
                if label == 'dialogue':
                    self.assertGreaterEqual(row['style']['dialogue_ratio'],.15)
                    self.assertEqual(final.split('\n\n')[1],draft.split('\n\n')[1])
                else:
                    self.assertEqual(final,body(60 if label=='long' else 20))
                    self.assertEqual(final.split('\n\n')[0],draft.split('\n\n')[0])
                requirements = json.loads(client.calls[1]['prompt'].split('## 本章要求\n')[1].split('\n\n## ')[0])
                self.assertEqual(requirements['actual_chars'],q1.chinese_count(draft))
                self.assertEqual(requirements['word_range'],[170,230])
                self.assertEqual(requirements['target_chars'],200)

    def test_failed_previous_and_current_complete_bodies_share_one_finish_payload(self):
        def long_body(label,filler):
            fact = '阿宁在院里拿着旧钥匙。' if label == '上章' else '阿宁带着旧钥匙走进长廊。'
            head,tail=label+'开头独标。'+fact,label+'完整末尾证据。'
            return head+filler*(2200-q1.chinese_count(head+tail))+tail
        previous,current=long_body('上章','甲'),long_body('本章','乙')
        self.book = Book('suspense-mystery',25,2200,'USER_INPUT_SENTINEL')
        carry = self.extraction(state={'objects':{'旧钥匙':{'owner':'阿宁','location':'院'}}})
        current_raw = self.extraction(previous_delta=carry,summary='阿宁带着旧钥匙走进长廊。',
                                      state={'characters':{'阿宁':{'location':'廊','present':True}}})
        client=CaptureClient(self.out,[previous,current],{'r2-ch1-finish':{'type':'日常'},'r2-ch2-finish':current_raw})
        first=self.run_chapter(client,rows=[],outline=dict(self.outline,number=1))
        self.assertTrue(first['state_failed'])
        self.assertEqual((self.out/'chapter-001.body.md').read_text(),previous)
        rows=[first]
        second=self.run_chapter(client,state=first['state'],rows=rows)
        self.assertEqual([c['stage'] for c in client.calls],
                         ['r2-ch1-draft','r2-ch1-finish','r2-ch2-draft','r2-ch2-finish'])
        finish=client.calls[-1]['prompt']
        self.assertIn(number_paragraphs(current),finish)
        self.assertIn(previous,finish)
        self.assertIn('上章完整末尾证据。',finish)
        self.assertIn('本章完整末尾证据。',finish)
        self.assertIn('## 上章失败顺带补提',finish)
        self.assertLess(len(finish),10000)
        self.assertTrue(load(self.out/'steps/r2-ch2-finish.json')['_previous_complete'])
        recovered=load(self.out/'chapters/001.json')
        self.assertTrue(recovered['carry_attempted'])
        self.assertTrue(recovered['state_recovered'])
        self.assertTrue(recovered['state_failed'])  # Original failure remains visible.
        self.assertEqual(second['state']['objects']['旧钥匙']['location'],'院')
        self.assertEqual(second['state']['characters']['阿宁']['location'],'廊')
        self.assertFalse(second['state_failed'])
        self.assert_saved(second,current)
        self.assertEqual((self.out/'chapter-001.body.md').read_text(),previous)

    def test_budget_exceptions_do_not_become_format_retries(self):
        for stage in ('r2-ch2-draft','r2-ch2-finish','r2-ch2-final-state'):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as directory:
                self.out = Path(directory)
                save(self.out/'chapter-001.body.md',self.previous)
                old={'continuity':{'balances':{'寿命':{'amount':2,'unit':'天'}}}}
                before=deepcopy(old)
                replies={'r2-ch2-finish':self.extraction(patch={'after':1,'text':'她拿起钥匙。'}),
                         stage:RuntimeError('budget reached')}
                client=CaptureClient(self.out,[body(60)],replies)
                with self.assertRaisesRegex(RuntimeError,'budget'):
                    self.run_chapter(client,state=old)
                expected=['r2-ch2-draft','r2-ch2-finish','r2-ch2-final-state']
                self.assert_calls(client,expected[:expected.index(stage)+1])
                self.assertEqual(old,before)
                self.assertFalse((self.out/'chapters/002.json').exists())
                self.assertEqual(list((self.out/'steps').glob('*.retry.json')),[])

    def test_loose_extraction_parsing_and_stage_cache_preserve_grounded_facts(self):
        raw=self.extraction(payoff={'type':self.bible['canon']['payoff_menu'][0],'fulfilled':True,'pid':2})
        raw['state']['characters']['阿宁']['present']='true'
        serialized=json.dumps(raw,ensure_ascii=False)
        for reply in ('```json\n'+serialized+'\n```',serialized[:-1]+',"unfinished":"'):
            with self.subTest(reply=reply[-20:]), tempfile.TemporaryDirectory() as directory:
                self.out=Path(directory)
                client=CaptureClient(self.out,[],{'r2-ch2-finish':reply})
                args=(client,'r2-ch2-finish',body(60),{},self.bible,self.outline,[])
                value,failed,retries=q1.extract_state(*args)
                self.assertEqual((failed,retries),(False,0))
                self.assertIs(value['state']['characters']['阿宁']['present'],True)
                self.assertEqual(value['payoff']['quote'],body(60).split('\n\n')[1])
                self.assertEqual(load(self.out/'steps/r2-ch2-finish.json'),value)
                self.assertEqual(q1.extract_state(*args),(value,False,0))
                self.assert_calls(client,['r2-ch2-finish'])

    def test_acceptance_counts_new_dialogue_without_rewriting_history(self):
        client=CaptureClient(self.out,[body(60)])
        row=self.run_chapter(client)
        old=dict(row,number=1,style=dict(row['style'],dialogue_ratio=.05))
        before=deepcopy(old)
        result=q1.acceptance([old,row],[],client,self.bible,self.threads,{},base=1)
        self.assertEqual((result['new_dialogue20_pass'],result['new_chapters']),(1,1))
        self.assertEqual(result['generated'],2)
        self.assertEqual(q1.acceptance([old,row],[],client,self.bible,self.threads,{},base=0)['new_chapters'],2)
        self.assertEqual(old,before)
        self.assertNotIn('criteria',result)


if __name__ == '__main__':
    unittest.main()
