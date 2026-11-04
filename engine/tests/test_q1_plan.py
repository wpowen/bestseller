"""Pure Q1 parsing/rhythm tests plus local stage fixtures; no model or database."""
import copy
from pathlib import Path
import tempfile
import unittest

import yaml

from bookengine.config import Book
from bookengine import q1_plan as q1


ROOT = Path(__file__).resolve().parents[2]
PACKS = ('xuanhuan-power-fantasy', 'urban-power-reversal', 'suspense-mystery')


class StageFixture:
    """In-memory checkpoint interface; deliberately has no HTTP implementation."""
    prefix = ''

    def __init__(self, response='broken: ['):
        self.response, self.calls, self.cache = response, [], {}

    def stage(self, name, prompt, schema=None, **kwargs):
        if name not in self.cache:
            self.calls.append((name, prompt, kwargs))
            self.cache[name] = kwargs['parser'](self.response)
        return self.cache[name]


class ParsingTests(unittest.TestCase):
    def test_objects_fences_trailing_commas_and_lists(self):
        self.assertEqual(q1.parse('```yaml\nnumber: "3"\ntitle: 001章\n```'), {'number': 3, 'title': '001章'})
        self.assertEqual(q1.parse('{"title":"窗",}'), {'title': '窗'})
        for raw in ('[{"number": "3"}]', '```json\n[{"number":3}]\n```', '- number: 3'):
            self.assertEqual(len(q1.parse(raw)), 1)
            self.assertEqual(q1.integer(q1.parse(raw)[0]['number']), 3)

    def test_invalid_outputs_degrade_to_empty_object(self):
        for raw in ('broken: [', '', '只有正文', None, 17):
            self.assertEqual(q1.parse(raw), {})

    def test_missing_and_wrong_fields_default_without_discarding_good_values(self):
        value = q1.defaults({'title': '我的书', 'power': None, 'characters': 5}, q1.DATA['schemas']['prepare']['bible'])
        self.assertEqual(value['title'], '我的书')
        self.assertEqual(value['power'], {'cost': '', 'limit': ''})
        self.assertEqual(value['characters'], [])
        self.assertEqual(q1.integer(True, 4), 4)

    def test_thread_defaults_cover_every_volume_without_claiming_beats_happened(self):
        value = q1.normalize_threads({}, 4, 100, q1.DATA['profile'])
        self.assertEqual({x['kind'] for x in value['threads']}, set(q1.DATA['thread_kinds']))
        for line in value['threads']:
            self.assertEqual({x['volume'] for x in line['beats']}, {1, 2, 3, 4})
            self.assertTrue(all(x['description'] == '' for x in line['beats']))
            if line['kind'] in ('dark', 'romance'):
                self.assertTrue(all(sum(x['volume'] == n for x in line['beats']) >= 2 for n in range(1, 5)))
            if line['kind'] == 'hidden':
                self.assertTrue(all(x['reveal_not_before'] == 76 for x in line['beats']))

    def test_thread_ids_are_unique_and_gap_is_clamped(self):
        source = {'threads': [dict(id='same', kind=k, max_gap='0', beats=[dict(id='shared', volume='1')]) for k in ('dark', 'romance')]}
        result = q1.normalize_threads(source, 1, 25, {'romance_max_gap': 0})
        lines = result['threads']
        ids = [b['id'] for line in lines for b in line['beats']]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(lines), len({x['id'] for x in lines}))
        self.assertTrue(all(x['max_gap'] >= 1 for x in lines))
        self.assertEqual(source['threads'][0]['id'], 'same')

    def test_callbacks_only_use_first_volume_historical_events(self):
        history = [dict(chapter=n, summary=f'发生{n}') for n in (1, 2, 25, 26)]
        history += [dict(number=3, payoff='未来兑现', expected_dashboard={'strength': '强'}), {'summary': '无章号'}]
        self.assertEqual(q1.callbacks(history), [dict(chapter=n, event=f'发生{n}') for n in (1, 2, 25)])
        self.assertEqual(q1.callbacks({'events': history}), q1.callbacks(history))
        self.assertEqual(q1.callbacks([]), [])

    def test_callbacks_extract_completed_events_from_volume_history(self):
        events = [dict(chapter=n, event=f'实事{n}') for n in (2, 10, 24)]
        history = [dict(number=1, start=1, end=25, summary='整卷摘要不是第一章事件', events=events),
                   dict(number=2, start=26, end=50, summary='第二卷')]
        self.assertEqual(q1.callbacks(history), events)


class RhythmTests(unittest.TestCase):
    def test_all_pack_profiles_have_required_fields_and_original_anchors(self):
        for key in PACKS:
            profile = yaml.safe_load((ROOT / 'config/prompt_packs' / f'{key}.yaml').read_text())['q1']
            self.assertTrue(set(q1.DATA['profile']) <= profile.keys())
            self.assertGreaterEqual(len(profile['style_anchors']), 2)
            self.assertTrue(all(3 <= len([s for s in anchor.split('。') if s.strip()]) <= 6 for anchor in profile['style_anchors']))

    def test_100_chapters_have_no_triples_or_five_without_planned_payoff(self):
        for key in PACKS:
            profile = yaml.safe_load((ROOT / 'config/prompt_packs' / f'{key}.yaml').read_text())['q1']
            value = q1.schedule(1, 100, profile)
            for i in range(98):
                self.assertGreater(len({x['type'] for x in value[i:i+3]}), 1)
                self.assertGreater(len({x['hook_type'] for x in value[i:i+3]}), 1)
            for i in range(96):
                self.assertTrue(any(x['payoff_due'] for x in value[i:i+5]))
                self.assertLessEqual(sum(x['meeting_allowed'] for x in value[i:i+5]), 1)
            self.assertTrue(value[2]['payoff_due'])
            self.assertTrue(all('payoff' not in x and 'completed' not in x for x in value))

    def test_window_boundary_uses_previous_actual_rhythm(self):
        recent = [dict(number=n, type='对抗', hook_type='threat', payoff='', scenes=[]) for n in (8, 9, 10)]
        recent[-1]['scenes'] = [{'kind': 'meeting'}]
        original = copy.deepcopy(recent)
        value = q1.schedule(11, 20, dict(q1.DATA['profile'], payoff_max_gap=4), recent)
        self.assertNotEqual(value[0]['type'], '对抗')
        self.assertNotEqual(value[0]['hook_type'], 'threat')
        self.assertTrue(value[0]['payoff_due'])
        self.assertTrue(all(not x['meeting_allowed'] for x in value[:4]))
        self.assertTrue(value[4]['meeting_allowed'])
        self.assertEqual(recent, original)

    def test_bad_numeric_profile_is_tolerated_and_schedule_is_stable(self):
        profile = dict(q1.DATA['profile'], payoff_max_gap='bad', chapter_type_mix={'对抗': 'oops'})
        self.assertEqual(q1.schedule(26, 35, profile), q1.schedule(26, 35, profile))
        self.assertEqual(q1.schedule(26, 25, profile), [])

    def test_recent_completed_rows_use_meeting_scenes_count(self):
        recent = [dict(number=10, meeting_scenes=1, scenes=2, payoff='救人')]
        value = q1.schedule(11, 15, q1.DATA['profile'], recent)
        self.assertEqual([x['meeting_allowed'] for x in value], [False]*4+[True])


class StageInterfaceTests(unittest.TestCase):
    def test_prepare_loads_profile_and_never_rewrites_yaml_on_resume(self):
        registry = yaml.safe_load((ROOT / 'engine/data/growth_payoff.yaml').read_text())
        canon = dict(registry['packs'][PACKS[0]], solution_aliases=registry['solution_aliases'],
                     seed='新书', scheduling=registry.get('scheduling', {}))
        self.assertTrue(canon['solution_aliases'])
        fixture = StageFixture(yaml.safe_dump(dict(bible=dict(title='新书',
            characters=[dict(name='周远舟', role='protagonist', voice='短句直说', desire='救回妹妹'),
                        dict(name='赵听禾', role='support', voice='爱反问', desire='保住药铺'),
                        dict(name='王见微', role='antagonist', voice='慢声威胁', desire='夺下城门')],
            initial_dashboard=dict(strength='一阶'), summary='字'*2000), threads=[]), allow_unicode=True))
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'book'
            out.mkdir()
            args = (fixture, Book(PACKS[0], 100, 2200, '自定剧情'), out, ROOT/'config')
            bible, threads, profile = q1.prepare(*args)
            self.assertEqual(bible['canon'], canon)
            self.assertEqual(profile['canon'], canon)
            self.assertEqual(bible['initial_dashboard']['strength'], canon['ladder']['strength'][0])
            self.assertEqual(bible['initial_dashboard']['status'], canon['ladder']['status'][0])
            self.assertEqual(bible['initial_dashboard']['wealth'], '0' + canon['ladder']['wealth_unit'])
            self.assertEqual(len(bible['summary']), 1800)
            self.assertEqual([(x['start'], x['end']) for x in bible['volumes']], [(1,25),(26,50),(51,75),(76,100)])
            paths = [out/name for name in ('story_bible.yaml', 'threads.yaml')]
            self.assertEqual(yaml.safe_load(paths[0].read_text())['canon']['solution_aliases'],
                             registry['solution_aliases'])
            before = [(p.read_bytes(), p.stat().st_mtime_ns) for p in paths]
            self.assertEqual(q1.prepare(*args), (bible, threads, profile))
            self.assertEqual([(p.read_bytes(), p.stat().st_mtime_ns) for p in paths], before)
            self.assertEqual(len(fixture.calls), 1)
            self.assertEqual(fixture.calls[0][0], 'plan-q1-prepare')
            self.assertIn('自定剧情', fixture.calls[0][1])
            self.assertFalse(fixture.relaxed)
            # A partially saved pair reuses the one stage response, preserving the bible.
            paths[1].unlink()
            q1.prepare(*args)
            self.assertEqual(len(fixture.calls), 1)
            self.assertEqual((paths[0].read_bytes(), paths[0].stat().st_mtime_ns), before[0])

    def test_volume_has_fixed_range_beats_and_only_historical_callbacks(self):
        fixture = StageFixture('{"number":99,"start":999,"expected_dashboard":null}')
        threads = q1.normalize_threads({}, 4, 100, q1.DATA['profile'])
        bible = {'characters': [], 'volumes': [dict(number=2, stage='边城', goal='救人', cost='受伤')]}
        value = q1.volume(fixture, bible, threads, 2, 100, {}, [dict(chapter=1, summary='救下同伴')], ['人物要有脾气'])
        self.assertEqual((value['number'], value['start'], value['end']), (2,26,50))
        self.assertEqual(value['stage'], '边城')
        self.assertTrue(value['beats'])
        self.assertTrue(all(x['volume'] == 2 or x['overdue'] for x in value['beats']))
        self.assertTrue(any(x['volume'] == 1 and x['overdue'] for x in value['beats']))
        self.assertEqual(value['callback_events'], [dict(chapter=1,event='救下同伴')])
        self.assertEqual(value['expected_dashboard']['strength'], '')

    def test_outlines_accept_bare_list_reorder_and_fill_without_inventing_payoff(self):
        fixture = StageFixture('[{"number":3,"title":"兑现","payoff":"夺回机会"},{"number":1,"title":"受阻","scenes":[{}]}]')
        value = q1.outlines(fixture, {}, {'threads': []}, q1.DATA['profile'], {'number':1,'end':25}, 1, 3, {}, [], [], [])
        self.assertEqual([x['number'] for x in value], [1,2,3])
        self.assertEqual([x['title'] for x in value], ['受阻','','兑现'])
        self.assertEqual(value[1]['payoff'], '')
        self.assertEqual(value[0]['scenes'][0]['obstacle'], '')
        self.assertEqual(value[2]['payoff'], '夺回机会')
        self.assertEqual(fixture.calls[0][2]['max_attempts'], 1)

    def test_model_parser_distinguishes_empty_defaults_from_broken_format(self):
        self.assertEqual(q1.model_value('{}'),{})
        self.assertEqual(q1.model_value('[]'),[])
        with self.assertRaises(ValueError): q1.model_value('broken: [')

    def test_chapter_100_receives_three_completed_callbacks_not_future_changes(self):
        fixture = StageFixture('{}')
        history = [dict(chapter=n, summary=f'事件{n}') for n in (1,12,25)]
        value = q1.outlines(fixture, {'chapters': 100}, {'threads': []}, q1.DATA['profile'], {'number':4,'end':100}, 96, 100, {}, [], history, [])
        self.assertEqual([x['number'] for x in value], [96, 97, 98, 99, 100])
        self.assertTrue(all(x['callback_events'] == [] for x in value[:-1]))
        self.assertEqual(value[-1]['callback_events'], q1.callbacks(history))
        self.assertEqual(len(value[-1]['callback_events']), 3)
        self.assertIn('尾章必须融入当下冲突的首卷事实与原文', fixture.calls[0][1])

    def test_empty_window_never_calls_stage(self):
        fixture = StageFixture()
        self.assertEqual(q1.outlines(fixture, {}, {}, {}, {'end':25}, 26, 30, {}, [], [], []), [])
        self.assertEqual(fixture.calls, [])


if __name__ == '__main__':
    unittest.main()
