import copy
import unittest

from bookengine.longmemory import compact_state, extract_summary, long_context


class LongMemoryTests(unittest.TestCase):
    def test_location_keeps_only_place_or_container_and_does_not_mutate_raw_response(self):
        value = {'characters': {'灰牙': {'location': '验收槽区域，爪伤未愈且背脊有白痕', 'present': True}},
                 'objects': {'母种': {'location': '旧轨车槽内，随矿车进入断崖验收槽区'}}}
        state = compact_state(value)
        self.assertEqual(state['characters']['灰牙']['location'], '验收槽区域')
        self.assertEqual(state['objects']['母种']['location'], '旧轨车槽内')
        self.assertIn('爪伤', value['characters']['灰牙']['location'])

    def test_aliases_and_slim_schema(self):
        raw = dict(summary='甲回家。', who_is_where=[dict(who='甲', where='屋', present=False,
                   quote='原句', paragraph=2)], items={'伞': dict(place='屋', holder='甲')},
                   time=dict(day=1, minutes=12, quote='原句'), unresolved=['信'],
                   relations=[dict(pair=['甲', '乙'], state='互相信任。昨天争吵过。', quote='原句')])
        self.assertEqual(compact_state(raw), dict(
            characters={'甲': dict(location='屋', present=False)},
            objects={'伞': dict(location='屋', owner='甲')}, clock=dict(day=1, minute=12),
            foreshadows={'信': 'open'}, relationships=['甲 / 乙：互相信任。']))
        self.assertEqual(extract_summary(raw), '甲回家。')
        self.assertEqual(extract_summary({'chapter_summary': ' 后续 '}), '后续')
        self.assertEqual(extract_summary(None), '')

    def test_deltas_preserve_facts_and_do_not_mutate_inputs(self):
        previous = dict(characters={'甲': dict(location='院', present=True, quote='不保留')},
                        objects={'伞': dict(location='院', owner='甲')},
                        clock=dict(day=2, minute=10), relationships=['甲与乙互信。'],
                        foreshadows={'信': 'open'})
        delta = [dict(characters=[{'甲': {'present': False}}]),
                 dict(objects=[dict(name='伞', location='屋')], clock=dict(minute=30)),
                 dict(foreshadows=[dict(name='信', status='closed')])]
        before = copy.deepcopy((previous, delta))
        result = compact_state(delta, previous)
        self.assertEqual(result['characters']['甲'], dict(location='院', present=False))
        self.assertEqual(result['objects']['伞'], dict(location='屋', owner='甲'))
        self.assertEqual(result['clock'], dict(day=2, minute=30))
        self.assertEqual(result['foreshadows'], {'信': 'closed'})
        self.assertEqual(result['relationships'], ['甲与乙互信。'])
        result['relationships'].append('其他。')
        self.assertEqual((previous, delta), before)

    def test_unknowns_and_invalid_values_never_invent_facts(self):
        result = compact_state(dict(characters={'甲': '屋', '乙': {'present': False},
                                   '丙': {'present': 'yes'}, '丁': {'quote': '正文'}},
                                   objects={'伞': {'owner': '甲'}},
                                   clock={'day': True, 'minute': 1500},
                                   foreshadows={'信': 'maybe'}, relationships=[{'quote': '正文'}]))
        self.assertEqual(result['characters'], {'甲': dict(location='屋', present=None),
                                               '乙': dict(location=None, present=False)})
        self.assertEqual(result['objects'], {'伞': dict(location=None, owner='甲')})
        self.assertIsNone(result['clock'])
        self.assertEqual(result['foreshadows'], {})
        self.assertEqual(result['relationships'], [])
        self.assertEqual(compact_state(result), result)
        self.assertEqual(compact_state(None), compact_state([None, '', {}]))

    def test_explicit_snapshot_replaces_stale_relationship_and_clock_clear(self):
        previous = dict(relationships=['甲与乙互信。'], clock=dict(day=0, minute=0))
        result = compact_state(dict(relationships={'甲与乙': '已决裂。旧事略。'}, clock=None), previous)
        self.assertEqual(result['relationships'], ['甲与乙：已决裂。'])
        self.assertIsNone(result['clock'])
        self.assertEqual(compact_state({'relationships': []}, previous)['relationships'], [])
        self.assertEqual(compact_state({'clock': '晚上'}, previous)['clock'], previous['clock'])

    def test_context_counts_formatting_and_retains_priority(self):
        sections = [('本章纲', '甲🙂'), ('上章结尾', '乙。' + '余' * 50), ('其余', '短')]
        expected = '## 本章纲\n甲🙂\n\n## 上章结尾\n乙。'
        self.assertEqual(long_context(sections, len(expected) + 3), expected)
        self.assertEqual(long_context(sections, 7), '## 本章纲\n')
        full = '## 甲\n正文\n\n## 乙\n结尾'
        self.assertEqual(long_context([('甲', '正文'), ('乙', '结尾')], len(full)), full)

    def test_sentence_paragraph_and_hard_truncation(self):
        for body, prefix in [('首句。' + '余' * 100, '首句。'),
                             ('首段\n\n' + '余' * 100, '首段\n\n'),
                             ('First. ' + 'x' * 100, 'First.')]:
            with self.subTest(body=body[:10]):
                result = long_context([('', body)], len(prefix) + 2)
                self.assertEqual(result, prefix)
        self.assertEqual(long_context([('', '🙂' * 100)], 13), '🙂' * 13)
        self.assertEqual(len(long_context([('', '界' * 40_000)], 90_000)), 30_000)
        self.assertEqual(len(long_context([('', '界' * 40_000)], 30_000 - 150)), 29_850)

    def test_malformed_context_and_budget_are_nonthrowing(self):
        for budget in (None, -1, 0, True, 1.5, '20'):
            self.assertEqual(long_context([('章', '正文')], budget), '')
        for sections in (None, 12, 'bad'):
            self.assertEqual(long_context(sections, 100), '')
        self.assertEqual(long_context([None, (), (1, '正文'), ('甲', None), ('乙', '正文')], 100),
                         '## 乙\n正文')
        for budget in range(100):
            result = long_context([('长标题', '甲。\n\n' + '余' * 200)], budget)
            self.assertIsInstance(result, str)
            self.assertLessEqual(len(result), budget)


if __name__ == '__main__':
    unittest.main()
