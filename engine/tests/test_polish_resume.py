"""Adoption/resume regressions: real reducers/stage cache, memory files, no network."""
from contextlib import ExitStack, redirect_stdout
from copy import deepcopy
from dataclasses import replace
import io
import json
from pathlib import Path
import re
import unittest
from unittest.mock import patch

import yaml

from bookengine import client as client_module, q1, q1_plan, storage
from bookengine.client import Client
from bookengine.config import Book
from bookengine.polish_checks import scan as scan_continuity
from bookengine.q1_quality import merge_state, metrics


BODY = '“' + '我陪你回家' * 12 + '。”\n\n' + '她推开门' * 35 + '。'
CONTINUITY = {'blocked': ['天眼', '遁术'], 'limbs': {'左手': '已废'},
              'balances': {'寿命': {'amount': 65, 'unit': '天'}}}


class LocalClient(Client):
    """Bypass credential setup; retain Client.stage and replace only HTTP request."""
    def __init__(self, out, replies=()):
        self.out, self.replies = out, dict(replies)
        self.calls, self.prefix, self.glossary = [], '', {}
        self.history_path = out / 'calls.json'
        self.context_limit, self.clip_context, self.relaxed = 10000, False, True

    def request(self, stage, prompt, cap, effort, attempt):
        if stage not in self.replies:
            raise AssertionError('Unscripted model request: ' + stage)
        self.calls.append(dict(seq=len(self.calls) + 1, stage=stage, status='ok', prompt=prompt,
            attempt=attempt, input_chars=len(prompt), input_bytes=len(prompt.encode()),
            output_token_cap=cap, usage=dict(prompt_tokens=0, completion_tokens=0, total_tokens=0)))
        value = self.replies[stage]
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


class PolishResumeTests(unittest.TestCase):
    def setUp(self):
        self.out = Path('/__polish_resume_memory__/book')
        self.files = {}
        self.book = Book('suspense-mystery', 1, 200, '本地回归')
        self.bible = dict(title='本地回归', characters=[dict(name='林宁', role='protagonist')],
                          volumes=[dict(number=1, start=1, end=1)])
        self.bible['canon'] = deepcopy(q1_plan.GROWTH['packs'][self.book.prompt_pack])
        self.bible['canon']['hook_types'] = list(q1.PLAN_DATA['hook_types'])
        self.old = {'characters': {'林宁': {'location': '院', 'present': True}},
                    'continuity': deepcopy(CONTINUITY)}
        self.put(q1.DATA, yaml.safe_dump({'b': '', 'review': ''}, allow_unicode=True))
        self.put(self.out / 'story_bible.yaml', yaml.safe_dump(self.bible, allow_unicode=True))
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        real_read, real_exists, real_glob, real_open = Path.read_text, Path.exists, Path.glob, Path.open

        def virtual(path):
            return path.is_relative_to(self.out.parent)

        def read(path, *args, **kwargs):
            if path in self.files:
                return self.files[path]
            if virtual(path):
                raise FileNotFoundError(str(path))
            return real_read(path, *args, **kwargs)

        def guarded_open(path, mode='r', *args, **kwargs):
            if any(flag in mode for flag in 'wax+'):
                raise AssertionError('Unexpected disk write: ' + str(path))
            return real_open(path, mode, *args, **kwargs)

        def glob(path, pattern):
            if virtual(path):
                return iter(sorted(p for p in self.files
                    if p.is_relative_to(path) and p.relative_to(path).match(pattern)))
            return real_glob(path, pattern)

        for target, replacement in (('read_text', read), ('open', guarded_open), ('glob', glob),
                ('exists', lambda p: p in self.files if virtual(p) else real_exists(p))):
            self.stack.enter_context(patch.object(Path, target, replacement))
        for module in (q1, q1_plan, client_module, storage):
            self.stack.enter_context(patch.object(module, 'save', self.put))
        for target in ('socket.create_connection', 'socket.socket.connect',
                       'urllib.request.urlopen', 'bookengine.client.Client.request'):
            self.stack.enter_context(patch(target, side_effect=AssertionError('Network forbidden')))
        # These are tested separately; this suite exercises adoption, not screening.
        self.stack.enter_context(patch.object(q1, 'scan', return_value=[]))
        self.stack.enter_context(patch.object(q1, 'locate', return_value=[]))

    def put(self, path, value):
        self.files[Path(path)] = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)

    def get(self, path):
        return json.loads(self.files[Path(path)])

    def extracted(self, **extra):
        return dict(dict(type='日常', hook_type=q1.PLAN_DATA['hook_types'][0],
                    payoff={'type': '无兑现', 'fulfilled': False, 'quote': ''},
                    state={}, edits=[]), **extra)

    def amend(self, reply, text='林宁留在院里。'):
        client = LocalClient(self.out)
        client.glossary = {'旧灯': {'definition': '用来照明', 'first_chapter': 1}}
        before = deepcopy((self.old, self.bible, reply, self.files, client.glossary))
        candidates=[dict(id=i,categories=['adoption-fixture']) for i,_ in enumerate(q1._sentences(text),1)]
        with patch.object(q1,'locate',return_value=candidates), patch.object(q1, 'ask', side_effect=AssertionError('amend must be pure')):
            body, detail = q1.amend(text, reply, self.bible, self.old, {'word_range': [0, 10000]})
        self.assertEqual((self.old, self.bible, reply, self.files, client.glossary), before)
        self.assertEqual(client.calls, [])
        self.assertFalse({'state', 'summary', 'terms', 'advances', 'events'} & detail.keys())
        return body, detail, client

    def test_empty_candidates_skip_model_and_non_candidate_sentence_is_preserved(self):
        client=LocalClient(self.out)
        text='林宁留在院里。'
        body,detail=q1.amend(text,{'edits':[]},self.bible,self.old,{})
        self.assertEqual((body,client.calls,detail['hits_before']),(text,[],0))
        text='他第一次清楚意识到她还在等。林宁留在院里。'
        from bookengine.precision import locate as actual_locate
        with patch.object(q1,'locate',side_effect=actual_locate):
            body,detail=q1.amend(text,dict(edits=[dict(id=2,text='林宁走到码头。')]),self.bible,self.old,{})
        self.assertEqual(body,text)
        self.assertTrue(detail['adoption_failed'])
        with patch.object(q1,'locate',side_effect=actual_locate):
            body,detail=q1.amend(text,dict(edits=[dict(id=1,text='他把伞递给她。')]),self.bible,self.old,{})
        self.assertEqual(body,'他把伞递给她。林宁留在院里。')
        self.assertFalse(detail['adoption_failed'])
        self.assertEqual(client.calls, [])

    def proposed(self, edits):
        return dict(edits=edits, state={'characters': {'林宁': {'location': '码头'}}},
            summary='林宁到了码头。', advances=[dict(thread='暗线', event='已到码头', done=True)],
            events=[{'event': '已到码头'}], actual_payoff='拿到船票',
            terms=[dict(term='院', definition='未采用修订中的定义')],
            opens_new_mainline=True, reason='未采用的结尾', double_or_reawakening=True)

    def assert_rejected(self, reply):
        body, detail, client = self.amend(reply)
        self.assertEqual(body, '林宁留在院里。')
        self.assertEqual(detail['edits'], [])
        self.assertTrue(detail['adoption_failed'])
        self.assertEqual(self.old['characters']['林宁']['location'], '院')
        self.assertEqual(self.old['continuity'], CONTINUITY)
        self.assertEqual(set(client.glossary), {'旧灯'})
        saved_bible = yaml.safe_load(self.files[self.out / 'story_bible.yaml'])
        self.assertEqual(saved_bible, self.bible)

    def test_partial_invalid_edits_reject_body_and_all_proposed_facts(self):
        valid = dict(id=1, text='林宁走到桥上。', reason='地点修正')
        invalid = dict(id=99, text='林宁去了码头。', reason='不存在的句号')
        for edits in ([valid, invalid], [invalid, valid], [valid, dict(valid)]):
            with self.subTest(edits=edits):
                self.assert_rejected(self.proposed(edits))

    def test_nonlist_edits_do_not_adopt_state_or_summary(self):
        for edits in (None, {'id': 1, 'text': '林宁去了码头。'}, 'invalid', 7):
            with self.subTest(edits=edits):
                self.assert_rejected(self.proposed(edits))

    def test_valid_edits_never_adopt_model_review_state_or_summary(self):
        reply = dict(edits=[dict(id='1', text='林宁走到桥上。', reason='地点修正')],
                     state={'characters': {'林宁': {'location': '桥'}}}, summary='林宁到了桥上。')
        body, detail, _ = self.amend(reply)
        self.assertEqual(body, '林宁走到桥上。')
        self.assertEqual(self.old['characters']['林宁']['location'], '院')
        self.assertEqual(len(detail['edits']), 1)

    def test_valid_empty_replacement_deletes_only_its_sentence(self):
        body, detail, _ = self.amend(dict(edits=[
            dict(id=2, text='', reason='这句没有独立信息')], state={}), '林宁推开门。林宁再次推开门。')
        self.assertEqual(body, '林宁推开门。')
        self.assertEqual(detail['edits'][0]['text'], '')

    def test_paragraph_markers_preserve_original_quotes_and_separate_candidate_sentence_ids(self):
        text = '“先别走。等雨停了再出发。”她握住门把。\r\n\r\n\t「我会等。你也别急。」他点头。'
        body, _, pure_client = self.amend({'edits': [], 'state': {}}, text)
        self.assertEqual(pure_client.calls, [])
        client = LocalClient(self.out, {'r2-ch1-finish': self.extracted()})
        candidates = [dict(id=2, text='等雨停了再出发。”', categories=['quote-fixture'])]
        with patch.object(q1, 'locate', return_value=candidates):
            _, failed, retries = q1.extract_state(client, 'r2-ch1-finish', text, self.old, self.bible, {'number': 1}, [])
        prompt = client.calls[0]['prompt']
        numbered = prompt.split('## 编号正文\n', 1)[1].split('\n\n## ', 1)[0]
        self.assertEqual(re.sub(r'\[P\d+\] ', '', numbered), text)
        self.assertEqual(re.findall(r'\[P\d+\]', numbered), ['[P1]', '[P2]'])
        self.assertNotRegex(numbered, r'\[S\d+\]')
        candidate_section = prompt.split('## 候选句S编号\n', 1)[1].split('\n\n## ', 1)[0]
        self.assertEqual(json.loads(candidate_section.split('\n只返回完整JSON，结构：', 1)[0])[0]['id'], 2)
        self.assertEqual((failed, retries, len(client.calls)), (False, 0, 1))
        self.assertEqual(body, text)

    def test_cached_quote_worsening_rejects_whole_bundle_without_new_request(self):
        cases = [('“先别走。雨停再出发。”\n\n林宁留在院里。', '“先别走。”'),
                 ('「先别走。雨停再出发。」\n\n林宁留在院里。', '「先别走。」'),
                 ('“先别走。林宁留在院里。', '““先别走。')]
        for index, (text, replacement) in enumerate(cases):
            with self.subTest(text=text):
                stage = f'cached-bad-quotes-{index}'
                cached = self.proposed([
                    dict(id=1, text=replacement, reason='错误地补引号'),
                    dict(id=len(q1._sentences(text)), text='林宁走到桥上。', reason='另一条修改')])
                path = self.out / 'steps' / f'{stage}.json'
                self.put(path, cached)
                cache_bytes, old = self.files[path], deepcopy(self.old)
                client = LocalClient(self.out)
                client.glossary = {'旧灯': {'definition': '用来照明'}}
                body, detail, _ = self.amend(self.get(path), text)
                self.assertEqual((body, detail['edits']), (text, []))
                self.assertEqual(self.old['continuity'], CONTINUITY)
                self.assertEqual(self.old['characters']['林宁']['location'], '院')
                self.assertTrue(detail['adoption_failed'])
                self.assertEqual(set(client.glossary), {'旧灯'})
                self.assertEqual((self.files[path], self.old, client.calls), (cache_bytes, old, []))

    def test_quote_guard_accepts_balanced_edits_repairs_and_nonworsening_old_damage(self):
        cases = [('“先别走。雨停再出发。”', 2, '等雨停再走。”', '“先别走。等雨停再走。”'),
                 ('“先别走。', 1, '“先别走。”', '“先别走。”'),
                 ('「先别走。', 1, '「先别走。」', '「先别走。」'),
                 ('“先别走。林宁守在院里。', 2, '林宁留在院里。', '“先别走。林宁留在院里。')]
        for text, number, replacement, expected in cases:
            with self.subTest(text=text):
                reply = dict(edits=[dict(id=number, text=replacement, reason='局部修订')],
                             state={'characters': {'林宁': {'location': '院'}}}, summary='林宁在院里等候。')
                body, detail, _ = self.amend(reply, text)
                self.assertEqual(body, expected)
                self.assertEqual(detail['edits'], [{k:e[k] for k in ('id','text')} for e in reply['edits']])

    def test_nested_limb_state_normalizes_and_injured_arm_blocks_hand_alias(self):
        description = '右臂折断，无法抬起'
        raw = {'continuity': {'limbs': {'林宁': {'右臂': description}}}}
        before = deepcopy((self.old, raw))
        state = merge_state(self.old, raw, self.bible)
        self.assertEqual(state['continuity']['limbs'], {'左手': '已废', '右臂': description})
        for text in ('林宁抬起右手。', '林宁用右臂挡住门。'):
            with self.subTest(text=text):
                hits = scan_continuity(text, self.bible['characters'], state)
                self.assertTrue(any('continuity_candidate' in h['categories'] for h in hits))
        for text in ('林宁没有抬起右手。', '周青抬起右手。', '林宁抬起右脚。'):
            with self.subTest(text=text):
                self.assertFalse(any('continuity_candidate' in h['categories']
                    for h in scan_continuity(text, self.bible['characters'], state)))
        healthy = merge_state(self.old, {'continuity': {'limbs': {'林宁': {'右臂': '活动正常'}}}}, self.bible)
        self.assertFalse(any('continuity_candidate' in h['categories']
            for h in scan_continuity('林宁抬起右手。', self.bible['characters'], healthy)))
        self.assertEqual((self.old, raw), before)

    def test_string_balance_normalizes_for_correct_and_incorrect_deductions(self):
        for source, amount in (('65天', '65'), ('六十五天', '六十五')):
            with self.subTest(source=source):
                raw = {'continuity': {'balances': {'寿命': source}}}
                before = deepcopy((self.old, raw))
                state = merge_state(self.old, raw, self.bible)
                self.assertEqual(state['continuity']['balances']['寿命'], {'amount': amount, 'unit': '天'})
                state_before = deepcopy(state)
                correct = scan_continuity('寿命扣减10天，剩余55天。', state=state)
                incorrect = scan_continuity('寿命扣减10天，剩余60天。', state=state)
                self.assertFalse(any('balance_candidate' in h['categories'] for h in correct))
                self.assertTrue(any('balance_candidate' in h['categories'] for h in incorrect))
                self.assertEqual(state, state_before)
                self.assertEqual((self.old, raw), before)

    def test_empty_or_mistyped_continuity_preserves_existing_facts(self):
        for continuity in (None, [], 'unknown', {}, {'blocked': [], 'limbs': {}, 'balances': {}},
                           {'blocked': {}, 'limbs': [], 'balances': None}):
            with self.subTest(continuity=continuity):
                raw = {'continuity': continuity}
                before = deepcopy((self.old, raw))
                result = merge_state(self.old, raw, self.bible)
                self.assertEqual(result['continuity'], CONTINUITY)
                self.assertEqual((self.old, raw), before)

    def test_nonempty_changes_preserve_other_constraints_and_require_explicit_release(self):
        raw = {'continuity': {'blocked': ['天眼', '读心'], 'limbs': {'右腿': '不可用'},
               'balances': {'灵石': {'amount': 3, 'unit': '枚'}}, 'released': ['天眼']}}
        before = deepcopy((self.old, raw))
        result = merge_state(self.old, raw, self.bible)['continuity']
        self.assertEqual(set(result['blocked']), {'遁术', '读心'})
        self.assertEqual(result['limbs'], {'左手': '已废', '右腿': '不可用'})
        self.assertEqual(result['balances'], {**CONTINUITY['balances'], '灵石': {'amount': 3, 'unit': '枚'}})
        self.assertEqual((self.old, raw), before)

    def test_legacy_null_continuity_can_finish_chapter_without_losing_body(self):
        old = dict(deepcopy(self.old), continuity=None)
        extract = dict(state={'continuity': {'blocked': [], 'limbs': {}, 'balances': {}}}, type='日常',
            hook_type=q1.PLAN_DATA['hook_types'][0], payoff={'type':self.bible['canon']['payoff_menu'][0],'fulfilled':True,'quote':BODY})
        client = LocalClient(self.out, {'r2-ch1-draft': BODY, 'r2-ch1-finish':extract})
        row = q1.chapter(client, self.book, self.bible, {'threads': []}, {},
            {'number': 1, 'start': 1, 'end': 1}, {'number': 1}, old, [], [], self.out, 'b', {'b': ''})
        self.assertEqual(row['status'], 'complete')
        self.assertEqual(row['state']['continuity'], {})
        self.assertEqual(self.files[self.out / 'chapter-001.body.md'], BODY)
        self.assertEqual(self.get(self.out / 'chapters/001.json'), row)
        self.assertIsNone(old['continuity'])
        self.assertEqual([c['stage'] for c in client.calls], ['r2-ch1-draft', 'r2-ch1-finish'])
        self.assertEqual((row['outside_calls'], row['state_retries']), (1, 0))

    def test_body_change_alone_triggers_one_final_state_from_adopted_text(self):
        raw = self.extracted(patch={'after': 1, 'text': '她在门口停了一下。'},
                             state={'characters': {'林宁': {'location': '码头'}}})
        final = self.extracted(state={'characters': {'林宁': {'location': '桥'}}}, summary='她在桥上停步。')
        client = LocalClient(self.out, {'r2-ch1-draft': BODY, 'r2-ch1-finish': raw,
                                       'r2-ch1-final-state': final})
        row = q1.chapter(client, self.book, self.bible, {'threads': []}, {},
            {'number': 1, 'start': 1, 'end': 1}, {'number': 1}, self.old, [], [], self.out, 'b', {'b': ''})
        expected = BODY.replace('\n\n', '\n\n她在门口停了一下。\n\n', 1)
        self.assertEqual(self.files[self.out / 'chapter-001.body.md'], expected)
        self.assertEqual([c['stage'] for c in client.calls],
                         ['r2-ch1-draft', 'r2-ch1-finish', 'r2-ch1-final-state'])
        self.assertIn('她在门口停了一下。', client.calls[-1]['prompt'])
        self.assertEqual(row['state']['characters']['林宁']['location'], '桥')
        self.assertEqual((row['outside_calls'], row['state_retries']), (2, 0))

    def test_rejected_bundle_keeps_body_and_does_not_extract_final_state(self):
        raw = self.extracted(edits=[dict(id=1, text='“我陪你回家。”'), dict(id=99, text='不存在的句子。')])
        client = LocalClient(self.out, {'r2-ch1-draft': BODY, 'r2-ch1-finish': raw})
        with patch.object(q1, 'locate', return_value=[dict(id=1, categories=['quote-fixture'])]):
            row = q1.chapter(client, self.book, self.bible, {'threads': []}, {},
                {'number': 1, 'start': 1, 'end': 1}, {'number': 1}, self.old, [], [], self.out, 'b', {'b': ''})
        self.assertEqual(self.files[self.out / 'chapter-001.body.md'], BODY)
        self.assertTrue(row['polish']['adoption_failed'])
        self.assertEqual([c['stage'] for c in client.calls], ['r2-ch1-draft', 'r2-ch1-finish'])
        self.assertEqual(row['outside_calls'], 1)

    def test_failed_finish_carries_old_failure_once_without_recover_or_schema_retry(self):
        old = self.cached_row()
        old.update(state_failed=True)
        self.put(self.out / 'chapters/001.json', old)
        self.put(self.out / 'chapter-001.body.md', BODY)
        client = LocalClient(self.out, {'r2-ch2-draft': BODY, 'r2-ch2-finish': {'state': {}},
                                       'r2-ch3-finish': self.extracted()})
        args = (client, replace(self.book, chapters=3), self.bible, {'threads': []}, {},
                {'number': 1, 'start': 1, 'end': 3}, {'number': 2}, old['state'], [], [old], self.out, 'b', {'b': ''})
        row = q1.chapter(*args)
        self.assertTrue(row['state_failed'])
        self.assertEqual((row['state_retries'], row['outside_calls']), (0, 1))
        self.assertTrue(old['carry_attempted'])
        self.assertFalse(old.get('state_recovered', False))
        self.assertEqual(row['state']['continuity'], old['state']['continuity'])
        self.assertEqual([c['stage'] for c in client.calls], ['r2-ch2-draft', 'r2-ch2-finish'])
        self.assertIn('## 上章失败顺带补提\n', client.calls[-1]['prompt'])
        before = deepcopy((old, self.files))
        self.assertEqual(q1.chapter(*args), row)
        self.assertEqual((old, self.files), before)
        q1.extract_state(client, 'r2-ch3-finish', BODY, old['state'], self.bible, {'number': 3}, [old])
        self.assertNotIn('## 上章失败顺带补提\n', client.calls[-1]['prompt'])
        self.assertEqual(self.files[self.out / 'chapter-001.body.md'], BODY)
        self.assertEqual(old, before[0])

    def cached_row(self):
        return dict(number=1, title='旧章', status='complete', fact_source='final_body',
            chars=200, within_band=True, summary='林宁到了桥上。', seconds=9.5,
            state=merge_state(self.old, {'characters': {'林宁': {'location': '桥'}}}, self.bible),
            style=metrics(BODY), scene_types=['daily'], scenes=1, meeting_scenes=0,
            scene_classification='model', narrative_classification='model', payoff_observed=True,
            type='日常', hook_type=q1.PLAN_DATA['hook_types'][0], payoff='保住住处',
            hard_conflicts=0, issues=[], r2_events=[], polish={}, dropped_fields=['林宁.desire'])

    def resume(self, completed=0, old_quality=None):
        row = self.cached_row()
        meta = dict(mode='Q1', book=self.book.record(), style='b', status='interrupted',
            started=storage.now(), completed_chapters=completed, execution_seconds=321,
            revision=dict(name=q1.REVISION, started=storage.now(), base_chapters=0, base_seconds=0))
        if old_quality is not None:
            meta['quality'] = deepcopy(old_quality)
        self.put(self.out / 'run.json', meta)
        self.put(self.out / 'chapters/001.json', row)
        self.put(self.out / 'chapter-001.body.md', BODY)
        self.put(self.out / 'state.json', row['state'] if completed else self.old)
        historical = [dict(seq=1, stage='legacy-draft', status='ok', attempt=1, input_chars=100,
                          usage=dict(prompt_tokens=17, completion_tokens=23, total_tokens=40))]
        self.put(self.out / 'calls.json', historical)
        client = LocalClient(self.out)
        client.calls = deepcopy(historical)
        old_body = self.files[self.out / 'chapter-001.body.md']
        with ExitStack() as stack, redirect_stdout(io.StringIO()):
            stack.enter_context(patch.object(q1, 'QClient', return_value=client))
            stack.enter_context(patch.object(q1, 'prepare', return_value=(self.bible, {'threads': []}, {})))
            stack.enter_context(patch.object(q1, 'volume', return_value=dict(number=1, start=1, end=1)))
            stack.enter_context(patch.object(q1, 'outlines', return_value=[{'number': 1}]))
            chapter = stack.enter_context(patch.object(q1, 'chapter', wraps=q1.chapter))
            stack.enter_context(patch.object(q1, 'ask', side_effect=AssertionError('Cached resume must not request')))
            export = stack.enter_context(patch.object(q1, 'export'))
            result = q1._run(self.book, self.out, None, self.out.parent / 'assets', 180, 'b', lambda: False)
        self.assertEqual(client.calls, historical)
        self.assertEqual(self.get(self.out / 'calls.json'), historical)
        self.assertEqual(self.files[self.out / 'chapter-001.body.md'], old_body)
        self.assertEqual(self.get(self.out / 'chapters/001.json')['seconds'], row['seconds'])
        self.assertGreaterEqual(result['execution_seconds'], 321)
        self.assertEqual(export.call_args.args[3], [BODY])
        return result, row, chapter

    def test_growth_stage_names_reach_fifty_and_keep_time_without_speed_stop(self):
        for seconds,expected in ((0,'complete'),(6000,'complete')):
            with self.subTest(seconds=seconds):
                book=replace(self.book,chapters=50)
                self.bible['initial_dashboard']={'strength':'辨认异常','wealth':'3200元'}
                for n in range(1,50):
                    row=dict(self.cached_row(),number=n,run_elapsed=0,seconds=120 if seconds else .01)
                    self.put(self.out/f'chapters/{n:03d}.json',row)
                    self.put(self.out/f'chapter-{n:03d}.body.md',BODY)
                self.put(self.out/'run.json',dict(mode='Q1',book=book.record(),style='b',
                    status='interrupted',started=storage.now(),completed_chapters=49,
                    execution_seconds=seconds,revision=dict(name=q1.REVISION,started=storage.now(),
                    base_chapters=0,base_seconds=seconds,initial_seconds=0)))
                stale=dict(number=1,start=1,end=25,before_dashboard={'strength':'stale'},after_dashboard={'strength':'stale'},feedback=[])
                self.put(self.out/'volumes/01-025.json',stale)
                client=LocalClient(self.out)
                final=dict(self.cached_row(),number=50)
                self.put(self.out/'chapter-050.body.md',BODY)
                with ExitStack() as stack, redirect_stdout(io.StringIO()):
                    for key,value in [('QClient',client),('prepare',(self.bible,{'threads':[]},{})),
                        ('volume',dict(number=2,start=26,end=50)),('outlines',[dict(number=50)]),
                        ('chapter',final)]:
                        stack.enter_context(patch.object(q1,key,return_value=value))
                    stack.enter_context(patch.object(q1,'export'))
                    result=q1._run(book,self.out,None,self.out.parent/'assets',180,'b',lambda:False)
                self.assertEqual(result['status'],expected)
                self.assertEqual(result['completed_chapters'],50)
                self.assertEqual(self.get(self.out/'volumes/01-025.json'),stale)
                memory=self.get(self.out/'volumes/02-050.json')
                self.assertEqual(memory['after_dashboard'],final['state'].get('dashboard',{}))
                self.assertEqual(memory['fact_source'],'final_body')
                self.assertNotIn('scores',memory)
                self.assertEqual(client.calls,[])
                self.assertGreaterEqual(result['execution_seconds'],seconds)
                if seconds:self.assertGreater(result['warnings'][-1]['average_seconds'],100)
                self.assertEqual(self.files[self.out/'chapter-001.body.md'],BODY)

    def test_unsettled_cached_last_chapter_repairs_missing_and_stale_accounting(self):
        for old_quality in (None, {'generated': 0, 'stale': True}):
            with self.subTest(old_quality=old_quality):
                self.files.pop(self.out / 'volumes/01-001.json', None)
                result, row, chapter = self.resume(old_quality=old_quality)
                self.assertEqual(result['status'], 'complete')
                self.assertEqual(result['completed_chapters'], 1)
                self.assertEqual(result['quality']['generated'], 1)
                self.assertEqual(result['acceptance'], result['quality'])
                self.assertEqual(result['dropped_fields']['1'], row['dropped_fields'])
                self.assertNotIn('stale', result['quality'])
                self.assertEqual(self.get(self.out / 'run.json'), result)
                self.assertEqual(self.get(self.out / 'state.json'), row['state'])
                memory = self.get(self.out / 'volumes/01-001.json')
                self.assertEqual((memory['fact_source'], memory['end']), ('final_body', 1))
                self.assertNotIn('scores', memory)
                self.assertEqual(memory['summary'], row['summary'])
                self.assertEqual(chapter.call_count, 1)

    def test_already_settled_cached_chapter_is_not_reviewed_again(self):
        quality = {'generated': 1, 'already_settled': True}
        result, row, chapter = self.resume(completed=1, old_quality=quality)
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['acceptance']['generated'], quality['generated'])
        self.assertEqual(result['acceptance']['chapter_chars'], [row['chars']])
        self.assertEqual(result['acceptance']['word_band_pass'], 1)
        self.assertEqual(self.get(self.out / 'chapters/001.json'), row)
        self.assertEqual(chapter.call_count, 0)

    def test_run_carries_previous_delta_in_current_finish_preserving_old_body(self):
        for paid in (True,False):
            with self.subTest(recovered_paid=paid):
                self.files.pop(self.out / 'chapters/001.recovery.json', None)
                book=replace(self.book,chapters=2)
                kind=self.bible['canon']['payoff_menu'][0]
                old=self.cached_row()
                old.update(state_failed=True,payoff_due=True,payoff_type=kind,payoff_fulfilled=False)
                old['state']['pending_payoff']=kind
                old['state']['continuity']['balances']['寿命']={'amount':2,'unit':'天'}
                self.put(self.out/'chapters/001.json',old)
                self.put(self.out/'chapter-001.body.md',BODY)
                self.put(self.out/'chapter-001.draft.md','不可重写的旧初稿。')
                self.put(self.out/'writing_outlines/001.json',dict(number=1,payoff_due=True,payoff_type=kind))
                self.put(self.out/'state.json',old['state'])
                self.put(self.out/'run.json',dict(mode='Q1',book=book.record(),style='b',status='interrupted',
                    started=storage.now(),completed_chapters=1,
                    revision=dict(name=q1.REVISION,started=storage.now(),base_chapters=1,base_seconds=0)))
                for path in list(self.files):
                    if path.name.startswith('r2-ch') or path==self.out/'chapters/002.json' or path.name.startswith('01-002'):
                        self.files.pop(path)
                recovery=dict(type='日常',hook_type=q1.PLAN_DATA['hook_types'][0],
                    payoff={'type':kind if paid else '无兑现','fulfilled':paid,'quote':BODY if paid else ''},
                    state={'continuity':{'balances':{'寿命':{'amount':1,'unit':'天'}}}})
                current=dict(type='日常',hook_type=q1.PLAN_DATA['hook_types'][1],state={},
                    payoff={'type':kind,'fulfilled':True,'quote':BODY},previous_delta=recovery)
                client=LocalClient(self.out,{'r2-ch2-draft':BODY,'r2-ch2-finish':current})
                with ExitStack() as stack,redirect_stdout(io.StringIO()):
                    stack.enter_context(patch.object(q1,'QClient',return_value=client))
                    stack.enter_context(patch.object(q1,'prepare',return_value=(self.bible,{'threads':[]},{})))
                    stack.enter_context(patch.object(q1,'volume',return_value=dict(number=1,start=1,end=2)))
                    stack.enter_context(patch.object(q1,'outlines',return_value=[{'number':2}]))
                    stack.enter_context(patch.object(q1,'export'))
                    writes=stack.enter_context(patch.object(q1,'save',wraps=self.put))
                    q1._run(book,self.out,None,self.out.parent/'assets',180,'b',lambda:False)
                stages=[c['stage'] for c in client.calls]
                self.assertEqual(stages,['r2-ch2-draft','r2-ch2-finish'])
                self.assertIn('## 上章失败顺带补提\n',client.calls[-1]['prompt'])
                self.assertIn(json.dumps(BODY,ensure_ascii=False),client.calls[-1]['prompt'])
                recovered=self.get(self.out/'chapters/001.recovery.json')
                next_row=self.get(self.out/'chapters/002.json')
                self.assertTrue(recovered['state_recovered'])
                self.assertTrue(recovered['carry_attempted'])
                self.assertEqual(recovered['payoff_fulfilled'],paid)
                self.assertTrue(next_row['payoff_fulfilled'])
                self.assertIsNone(next_row['state']['pending_payoff'])
                self.assertEqual((next_row['outside_calls'],next_row['state_retries']),(1,0))
                self.assertEqual(next_row['state']['continuity']['balances']['寿命'],{'amount':1,'unit':'天'})
                self.assertEqual(self.files[self.out/'chapter-001.body.md'],BODY)
                self.assertEqual(self.files[self.out/'chapter-001.draft.md'],'不可重写的旧初稿。')
                self.assertNotIn(self.out/'chapter-001.body.md',[Path(call.args[0]) for call in writes.call_args_list])

    def seed_extension(self):
        bible = dict(self.bible, volumes=[dict(number=1, start=1, end=25)],
                     characters=[dict(name='林宁', role='protagonist', voice='短句直说', desire='救回姐姐'),
                                 dict(name='周远舟', role='support', voice='总爱反问', desire='保住药铺'),
                                 dict(name='赵听禾', role='antagonist', voice='慢声威胁', desire='夺下城门')])
        self.put(self.out / 'story_bible.yaml', yaml.safe_dump(bible, allow_unicode=True))
        self.put(self.out / 'threads.yaml', yaml.safe_dump({'threads': []}))
        self.put(self.out / 'state.json', self.old)
        original = self.files[self.out / 'story_bible.yaml']
        return original

    def continuation(self, target):
        return {'bible': {'volumes': [dict(number=n, start=(n-1)*25+1, end=min(n*25, target))
                                     for n in range(1, (target+24)//25+1)]}}

    def test_successive_extensions_ignore_shared_legacy_files_and_stage_cache(self):
        original = self.seed_extension()
        legacy = self.continuation(50)
        self.put(self.out / 'continuation.yaml', yaml.safe_dump(legacy))
        self.put(self.out / 'steps/plan-q1-extend.json', legacy)
        client = LocalClient(self.out, {f'plan-q1-r2-extend-{n}': self.continuation(n) for n in (50, 75)})
        prior = {}
        for target in (50, 75):
            bible, _, _ = q1_plan.prepare(client, replace(self.book, chapters=target),
                self.out, self.out.parent / 'assets', design=True)
            self.assertEqual(max(v['end'] for v in bible['volumes']), target)
            self.assertIn(self.out / f'continuation-{target}.yaml', self.files.keys())
            self.assertIn(self.out / f'steps/plan-q1-r2-extend-{target}.json', self.files.keys())
            self.assertRegex(client.calls[-1]['prompt'],
                             rf'"previous_end"\s*:\s*{25 if target == 50 else 50}\b')
            for path, text in prior.items():
                self.assertEqual(self.files[path], text, '旧 continuation/stage 不得重写')
            prior.update({self.out / f'continuation-{target}.yaml':
                          self.files[self.out / f'continuation-{target}.yaml'],
                          self.out / f'steps/plan-q1-r2-extend-{target}.json':
                          self.files[self.out / f'steps/plan-q1-r2-extend-{target}.json']})
        self.assertEqual([c['stage'] for c in client.calls], ['plan-q1-r2-extend-50', 'plan-q1-r2-extend-75'])
        self.assertEqual(self.files[self.out / 'story_bible.yaml'], original)
        self.assertEqual(self.get(self.out / 'steps/plan-q1-extend.json'), legacy)

    def test_extension_recovers_matching_stage_without_a_second_request(self):
        self.seed_extension()
        self.put(self.out / 'continuation-50.yaml', yaml.safe_dump(self.continuation(50)))
        self.put(self.out / 'steps/plan-q1-r2-extend-50.json', self.continuation(50))
        self.put(self.out / 'steps/plan-q1-r2-extend-75.json', self.continuation(75))
        client = LocalClient(self.out)
        for attempt in range(2):
            with self.subTest(attempt=attempt), redirect_stdout(io.StringIO()):
                bible, _, _ = q1_plan.prepare(client, replace(self.book, chapters=75),
                    self.out, self.out.parent / 'assets', design=True)
                self.assertEqual(max(v['end'] for v in bible['volumes']), 75)
        self.assertIn(self.out / 'continuation-75.yaml', self.files.keys())
        self.assertEqual(client.calls, [])


if __name__ == '__main__':
    unittest.main()
