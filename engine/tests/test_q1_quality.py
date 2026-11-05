import copy
from collections import Counter
from pathlib import Path
import unittest

import yaml

from bookengine.contracts import choose_contract
from bookengine.q1_quality import merge_state, metrics, progress, volume_issues


class Q1QualityTests(unittest.TestCase):
    def test_basic_metrics_keep_dialogue_attribution_together(self):
        text = '他担心迟到。可是又不愿走。\n\n“等等！”他喊。\n\n他把杯子放下。'
        row = metrics(text)
        self.assertEqual(row, dict(chars=21, paragraphs=3, single_sentence_ratio=0.6667,
                                  average_paragraph_chars=7.0, dialogue_ratio=0.0952,
                                  environment_personification=0))
        self.assertEqual(metrics('“等等！”他喊。')['single_sentence_ratio'], 1)
        self.assertEqual(metrics(''), dict.fromkeys(row, 0))

    def test_merge_seeds_characters_preserves_facts_and_q1_sidecars(self):
        old = dict(characters={'周屿': {'location': '旧屋', 'present': True}},
                   dashboard={'wealth': '十金'}, ledger={'dark': {'last': 2, 'done': ['a']}},
                   object_aliases={'旧刃': '短剑'})
        bible = dict(characters=[{'name': '周屿', 'role': '主角'}, {'name': '阿宁'}],
                     initial_dashboard={'strength': '一阶', 'wealth': '无'})
        raw = dict(characters=3, dashboard={'status': '获认可'}, ledger={'dark': {'done': ['伪造']}},
                   objects={'旧刃': {'location': '屋内'}})
        snapshot = copy.deepcopy((old, raw, bible))
        result = merge_state(old, raw, bible)
        self.assertEqual(result['characters']['周屿'], old['characters']['周屿'])
        self.assertEqual(result['characters']['阿宁'], {'location': None, 'present': None})
        self.assertEqual(result['dashboard'], {'strength': '一阶', 'wealth': '十金', 'status': '获认可'})
        self.assertEqual(result['ledger'], old['ledger'])
        self.assertEqual(result['objects'], {'短剑': {'location': '屋内', 'owner': ''}})
        result['ledger']['dark']['done'].append('b')
        self.assertEqual((old, raw, bible), snapshot)

    def test_object_alias_chains_merge_latest_facts_and_cap_items(self):
        old = dict(objects={'剑': {'owner': '甲', 'location': '旧屋'}, '短剑': {'location': '院'}},
                   object_aliases={'旧刃': '短剑', '短剑': '剑'})
        raw = dict(objects={'旧刃': {'owner': '乙'}, **{f'杂物{i}': '架子' for i in range(30)}})
        result = merge_state(old, raw, {'key_objects': {'剑': {}}})
        self.assertEqual(result['objects']['剑'], {'owner': '乙', 'location': '院'})
        self.assertEqual(result['object_aliases'], {'旧刃': '剑', '短剑': '剑'})
        self.assertEqual(len(result['objects']), 12)
        cyclic = merge_state({}, {'objects': {'甲物': '屋'}, 'object_aliases': {'甲物': '乙物', '乙物': '甲物'}}, {})
        self.assertIn('甲物', cyclic['objects'])
        self.assertEqual(merge_state({}, {'objects': {'纸杯': {'location': '桌', 'key': False}}}, {})['objects'], {})

    def test_progress_uses_events_not_plans_and_preserves_inputs(self):
        threads = {'threads': [{'id': 'dark', 'kind': 'dark', 'beats': [{'id': 'd1', 'volume': 1}]}]}
        old = {'ledger': {'dark': {'last': 1, 'done': [], 'max_gap': 0, 'extra': 'keep'}}}
        review = {'advances': [{'thread': 'dark', 'beat': 'd1', 'done': True}]}
        snapshot = copy.deepcopy((old, review, threads))
        state, issues = progress(old, review, 12, {'chapters': 100}, threads, {})
        self.assertEqual(state['ledger']['dark']['last'], 1)
        self.assertEqual(state['ledger']['dark']['done'], [])
        self.assertEqual(state['ledger']['dark']['max_gap'], 11)
        self.assertIn('断更11章', issues[0])
        self.assertEqual((old, review, threads), snapshot)
        review['advances'][0]['event'] = '周屿从阿宁口中得知旧案。'
        state, issues = progress(old, review, 12, {'chapters': 100}, threads, {})
        self.assertEqual(state['ledger']['dark']['done'], ['d1'])
        self.assertEqual(state['ledger']['dark']['last'], 12)
        self.assertEqual(state['ledger']['dark']['extra'], 'keep')
        self.assertEqual(state['ledger']['dark']['max_gap'], 11)
        self.assertTrue(any('断更11章' in i for i in issues))

    def test_hidden_reveal_uses_absolute_chapter_and_ending_requires_done(self):
        threads = {'threads': [{'id': 'secret', 'kind': 'hidden', 'beats': [
            {'id': 'truth', 'volume': 2, 'reveal_not_before': 40}]}]}
        review = {'advances': [{'thread': 'secret', 'beat': 'truth', 'event': '揭开旧案真相', 'reveal': True}]}
        bible = {'volumes': [{'number': 1, 'end': 25}, {'number': 2, 'end': 50}]}
        state, issues = progress({}, review, 30, bible, threads, {})
        self.assertTrue(any('提前reveal' in i for i in issues))
        _, issues = progress(state, review, 40, bible, threads, {})
        self.assertFalse(any('提前reveal' in i for i in issues))
        _, issues = progress(state, {}, 50, bible, threads, {})
        self.assertTrue(any('结尾未走完' in i for i in issues))
        review['advances'][0]['done'] = True
        _, issues = progress(state, review, 50, bible, threads, {})
        self.assertEqual(issues, [])

    def test_romance_limit_and_future_threads(self):
        threads = [{'id': 'love', 'kind': 'romance'}, {'id': 'later', 'kind': 'dark',
                     'beats': [{'id': 'b', 'volume': 3}]}]
        _, issues = progress({}, {}, 9, {}, threads, {'romance_max_gap': 8})
        self.assertEqual(len(issues), 1)
        self.assertIn('love', issues[0])
        self.assertIn('上限8', issues[0])

    def test_volume_uses_actual_dashboard_not_expected_plan(self):
        old = {'dashboard': {'strength': '一阶', 'stage': '城'}}
        history = [{'number': 1, 'dashboard': {'stage': '城'}}, {'number': 2, 'dashboard': {'stage': '城'}}]
        self.assertEqual(len(volume_issues(old, old, {'number': 3, 'expected_dashboard': {'strength': '二阶'}}, history)), 2)
        self.assertEqual(volume_issues(old, {'dashboard': {'strength': '二阶', 'stage': '山'}}, 3, history), [])
        self.assertEqual(volume_issues(old, {'dashboard': {'wealth': '十金'}}, 3, history), ['同主舞台连用超过2卷'])

    def test_bad_dashboard_values_preserve_known_facts(self):
        previous = {'dashboard': {'strength': '二阶', 'stage': '城'}}
        state = merge_state(previous, {'dashboard': {'strength': None, 'stage': '', 'wealth': []}}, {})
        self.assertEqual(state['dashboard'], previous['dashboard'])
        self.assertEqual(volume_issues(previous, state, 1, []), ['卷末仪表盘未变化'])

    def test_missing_or_malformed_optional_fields_use_defaults(self):
        self.assertEqual(metrics(None)['chars'], 0)
        self.assertEqual(merge_state(None, ['bad'], {'protagonist': '周屿'})['characters']['周屿'],
                         {'location': None, 'present': None})
        self.assertEqual(progress(None, {'advances': 3}, None, None, None, None), ({'ledger': {}}, []))
        self.assertEqual(volume_issues(None, None, None, None), ['卷末仪表盘未变化'])


class GrowthContractTests(unittest.TestCase):
    def setUp(self):
        registry=yaml.safe_load((Path(__file__).parents[1]/'data/growth_payoff.yaml').read_text())
        self.canon=copy.deepcopy(registry['packs']['xuanhuan-power-fantasy'])
        self.types={'升级','打脸','收获','身份抬升','碾压'}

    def test_five_canonical_types_obey_every_sliding_ten_chapter_window(self):
        self.assertEqual(set(self.canon['payoff_menu']),self.types)
        for mode in ('actual','reserved'):
            rows=[]
            for number in range(1,61):
                saved=copy.deepcopy((self.canon,rows));contract=choose_contract(self.canon,rows,number)
                self.assertEqual((self.canon,rows),saved)
                self.assertTrue(contract['payoff_due']);self.assertIn(contract['payoff_type'],self.types)
                row=dict(number=number,hook_type=contract['hook_type'])
                if mode=='actual':row['payoff']=dict(type=contract['payoff_type'],fulfilled=True)
                else:row['reserved_payoff_type']=contract['payoff_type']
                rows.append(row)
                kinds=[r['payoff']['type'] if mode=='actual' else r['reserved_payoff_type'] for r in rows]
                if len(kinds)>=3:self.assertGreater(len(set(kinds[-3:])),1)
                if len(kinds)>=10:self.assertGreaterEqual(len(set(kinds[-10:])),3)
            self.assertEqual(set(kinds),self.types)

    def test_planned_reservations_and_actual_non_due_payoffs_both_occupy_quota(self):
        self.canon['payoff_menu'].append('境界突破')
        self.canon['payoff_subtype_to_type']['境界突破']='升级'
        for mode in ('reserved','actual','both'):
            rows=[dict(number=n,hook_type='眼前危险逼近') for n in (1,2)]
            for row in rows:
                if mode!='actual':row['reserved_payoff_type']='升级'
                if mode!='reserved':row.update(payoff_due=False,payoff=dict(type='境界突破',fulfilled=True))
            result=choose_contract(self.canon,rows,3)
            self.assertTrue(result['payoff_due']);self.assertNotEqual(result['payoff_type'],'升级')
            self.assertIn(result['payoff_type'],self.types)
        rows=[dict(number=n,reserved_payoff_type='升级' if n%2 else '打脸') for n in range(1,10)]
        self.assertNotIn(choose_contract(self.canon,rows,10)['payoff_type'],('升级','打脸'))

    def test_pending_debt_keeps_its_type_and_blocked_debt_is_not_relabelled(self):
        pending=self.canon['payoff_menu'][0]
        result=choose_contract(self.canon,[],2,pending=pending)
        self.assertTrue(result['payoff_due']);self.assertEqual(result['payoff_type'],pending)
        rows=[dict(number=n,payoff=dict(type=pending,fulfilled=True)) for n in (1,2)]
        saved=copy.deepcopy(rows);result=choose_contract(self.canon,rows,3,pending=pending)
        self.assertTrue(result['payoff_due']);self.assertNotEqual(result['payoff_type'],pending)
        self.assertIn('pending_contract_blocked',result['warnings']);self.assertEqual(rows,saved)
