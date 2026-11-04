import copy
import json
import unittest

from bookengine.longmemory import compact_state
from bookengine.serialstate import (MAX_ACTIVE, MAX_STATE_CHARS, manage_state,
                                    restore_entities, serialize_state)


class SerialStateTests(unittest.TestCase):
    def start(self, names=('失踪的信',), seen=(), **fields):
        return manage_state(dict(foreshadows={n: 'open' for n in names}, **fields),
                            {}, {}, 1, 100, seen=seen)

    def codes(self, issues):
        return [i['code'] for i in issues]

    def test_five_fields_stable_ids_and_prompt_metadata(self):
        state, meta, archive, issues = self.start(
            characters={'甲': {'location': '家', 'present': True}},
            objects={'伞': {'owner': '甲'}}, clock={'day': 1, 'minute': 5},
            relationships=['甲乙互信。之前不信。'], _meta={'never': 'context'})
        self.assertEqual(state, compact_state(state))
        self.assertEqual(set(state), {'characters', 'objects', 'clock', 'relationships', 'foreshadows'})
        self.assertEqual(state['foreshadows'], {'F001': 'open'})
        self.assertEqual(meta['foreshadows']['F001'], dict(
            description='失踪的信', aliases=[], status='open', opened_at=1, updated_at=1))
        self.assertEqual(meta['counts'], dict(active=1, closed_total=0, opened_total=1))
        self.assertEqual((archive, issues), ([], []))
        self.assertNotIn('失踪的信', serialize_state(state))

    def test_explicit_id_needs_description_and_next_id_is_monotonic(self):
        state, meta, archive, issues = manage_state(dict(foreshadows={
            'F005': {'description': '旧债', 'status': 'open'}, 'F008': 'open'}), {}, {}, 1, 100)
        self.assertEqual(state['foreshadows'], {'F005': 'open'})
        self.assertEqual(self.codes(issues), ['missing_description'])
        self.assertEqual(archive[0]['key'], 'F008')
        state, meta, _, _ = manage_state({'foreshadows': {'新债': 'open'}}, state, meta, 2, 100)
        self.assertEqual(state['foreshadows'], {'F005': 'open', 'F006': 'open'})

    def test_close_is_archived_counted_once_and_never_reopened(self):
        state, meta, _, _ = self.start()
        state, meta, archive, issues = manage_state(
            {'foreshadows': {'失踪的信': 'closed'}}, state, meta, 2, 100)
        self.assertEqual(state['foreshadows'], {})
        self.assertEqual(archive[0]['meta']['description'], '失踪的信')
        self.assertEqual(archive[0]['reason'], 'closed')
        self.assertEqual(meta['counts']['closed_total'], 1)
        state, meta, archive, issues = manage_state(
            {'foreshadows': {'F001': 'closed', '失踪的信': 'open',
                             'F002': {'status': 'open', 'description': '失踪的信'}}},
            state, meta, 3, 100)
        self.assertEqual(state['foreshadows'], {})
        self.assertEqual(self.codes(issues), ['reopen_closed', 'reopen_closed'])
        self.assertEqual(len(archive), 2)
        self.assertEqual(meta['counts'], dict(active=0, closed_total=1, opened_total=1))

    def test_close_requires_preexisting_open_even_with_descriptive_evidence(self):
        state, meta, _, _ = self.start()
        state, meta, archive, issues = manage_state({'foreshadows': {
            '新名': {'status': 'closed', 'description': '失踪的信'},
            'F099': 'closed', '失踪的信': 'maybe'}}, state, meta, 2, 100)
        self.assertEqual(state['foreshadows'], {'F001': 'open'})
        self.assertEqual(self.codes(issues), ['close_without_open', 'close_without_open',
                                              'invalid_thread_status'])
        self.assertEqual(meta['counts']['closed_total'], 0)
        self.assertEqual(len(archive), 3)

    def test_cap_rejects_thirteenth_without_losing_active_threads(self):
        state, meta, archive, issues = self.start(names=[f'线索{i}' for i in range(13)])
        self.assertEqual(len(state['foreshadows']), MAX_ACTIVE)
        self.assertEqual(self.codes(issues), ['active_cap'])
        self.assertEqual(archive[0]['key'], '线索12')
        # Close-first processing makes the result independent of map key order.
        state, meta, archive, issues = manage_state(
            {'foreshadows': {'新线索': 'open', 'F001': 'closed'}}, state, meta, 2, 100)
        self.assertEqual(len(state['foreshadows']), MAX_ACTIVE)
        self.assertNotIn('F001', state['foreshadows'])
        self.assertIn('F013', state['foreshadows'])
        self.assertEqual(meta['counts']['closed_total'], 1)
        self.assertEqual(issues, [])

    def test_ending_exact_boundary_rounding_and_final_target(self):
        state, meta, _, _ = self.start(names=['一', '二', '三', '四'])
        state, meta, _, issues = manage_state({'foreshadows': {'九十': 'open'}}, state, meta, 90, 100)
        self.assertEqual(issues, [])
        state, meta, archive, issues = manage_state(
            {'foreshadows': {'九十一': 'open', 'F001': 'closed'}}, state, meta, 91, 100)
        self.assertEqual(self.codes(issues), ['ending_new_thread'])
        self.assertEqual(archive[-1]['reason'], 'rejected_ending_new_thread')
        state, meta, _, issues = manage_state({}, state, meta, 100, 100)
        self.assertEqual(self.codes(issues), ['ending_open_target'])
        self.assertEqual(meta['counts']['active'], 4)
        state, meta, _, issues = manage_state({'foreshadows': {'F002': 'closed'}}, state, meta, 100, 100)
        self.assertEqual(issues, [])
        self.assertEqual(meta['counts']['active'], 3)
        _, _, _, issues = manage_state({'foreshadows': {'尾声': 'open'}}, {}, {}, 91, 101)
        self.assertEqual(self.codes(issues), ['ending_new_thread'])

    def test_alias_names_persist_and_id_merge_is_not_a_fake_closure(self):
        state, meta, _, _ = self.start(names=['信的来处', '信件来源'])
        state, meta, archive, issues = manage_state({}, state, meta, 10, 100,
                                                   aliases={'信件来源': 'F001', '谁寄了信': 'F001'})
        self.assertEqual(state['foreshadows'], {'F001': 'open'})
        self.assertEqual(archive[0]['reason'], 'alias_merged')
        self.assertEqual(meta['foreshadows']['F002']['target'], 'F001')
        self.assertEqual(meta['counts'], dict(active=1, closed_total=0, opened_total=2))
        self.assertEqual(issues, [])
        meta = json.loads(json.dumps(meta, ensure_ascii=False))
        state, meta, archive, issues = manage_state(
            {'foreshadows': {'谁寄了信': 'closed'}}, state, meta, 11, 100)
        self.assertEqual(state['foreshadows'], {})
        self.assertEqual(meta['counts']['closed_total'], 1)
        state, meta, _, issues = manage_state({'foreshadows': {'F002': 'open'}}, state, meta, 12, 100)
        self.assertEqual(self.codes(issues), ['reopen_closed'])

    def test_unknown_closed_targets_and_id_aliases_are_rejected(self):
        state, meta, _, _ = self.start(names=['旧线', '活线'])
        state, meta, _, _ = manage_state({'foreshadows': {'F001': 'closed'}}, state, meta, 2, 100)
        state, meta, _, issues = manage_state({}, state, meta, 3, 100, aliases={
            '无目标': 'F999', '旧线': 'F002', '新名': 'F001', 'F999': 'F002'})
        self.assertEqual(state['foreshadows'], {'F002': 'open'})
        self.assertEqual(self.codes(issues), ['invalid_alias', 'invalid_alias', 'invalid_alias',
                                              'unknown_alias_id'])
        _, _, _, issues = manage_state({'foreshadows': {
            'F008': {'status': 'open', 'description': '活线'}}}, state, meta, 4, 100)
        self.assertEqual(self.codes(issues), ['alias_required'])

    def test_model_confirmed_closed_synonym_blocks_a_new_id_description(self):
        state, meta, _, _ = self.start(names=['信的来处'])
        state, meta, _, _ = manage_state({'foreshadows': {'F001': 'closed'}}, state, meta, 2, 100)
        # Caller records an explicit model assertion; the reducer does no semantic guessing.
        meta['foreshadows']['F001']['aliases'].append('神秘寄信者的身份')
        state, meta, archive, issues = manage_state({'foreshadows': {
            'F002': {'status': 'open', 'description': '神秘寄信者的身份'}}}, state, meta, 3, 100)
        self.assertEqual(self.codes(issues), ['reopen_closed'])
        self.assertEqual(state['foreshadows'], {})
        self.assertEqual(meta['counts'], dict(active=0, closed_total=1, opened_total=1))
        self.assertEqual(archive[0]['reason'], 'rejected_reopen_closed')

    def test_character_attendance_archives_only_after_twenty_absent_chapters(self):
        state, meta, _, _ = self.start(characters={'甲': {'location': '屋', 'present': True}})
        state, meta, archive, _ = manage_state(state, state, meta, 21, 100)
        self.assertIn('甲', state['characters'])
        self.assertEqual(archive, [])
        state, meta, archive, _ = manage_state(state, state, meta, 22, 100)
        self.assertNotIn('甲', state['characters'])
        self.assertNotIn('甲', meta['characters'])
        self.assertEqual(archive[0]['reason'], 'absent_20')
        self.assertEqual(archive[0]['value'], {'location': '屋', 'present': True})
        self.assertEqual(archive[0]['meta']['last_seen'], 1)
        # A real appearance refreshes attendance and permits return from archive.
        state, meta, _, _ = manage_state({'characters': {'甲': '院'}}, state, meta, 23, 100, seen=['甲'])
        state, meta, archive, _ = manage_state({}, state, meta, 44, 100, seen=['甲'])
        self.assertIn('甲', state['characters'])
        self.assertEqual(meta['characters']['甲']['last_seen'], 44)
        self.assertEqual(archive, [])

    def test_retired_items_archive_full_state_and_leave_context(self):
        state, meta, _, _ = self.start(objects={'伞': {'location': '屋', 'owner': '甲'}, '钥匙': '院'})
        state, meta, archive, issues = manage_state({}, state, meta, 2, 100, item_retired=['伞'])
        self.assertEqual(state['objects'], {'钥匙': {'location': '院'}})
        self.assertEqual(archive[0]['reason'], 'retired')
        self.assertEqual(archive[0]['value'], {'location': '屋', 'owner': '甲'})
        self.assertNotIn('伞', meta['objects'])
        self.assertEqual(issues, [])
        state, meta, archive, _ = manage_state({'objects': {'伞': '院'}}, state, meta, 3, 100)
        self.assertNotIn('伞', state['objects'])
        self.assertEqual(meta['retired_objects'], ['伞'])
        self.assertEqual(archive[0]['reason'], 'retired')

    def test_malformed_alias_map_cannot_crash_or_change_open_threads(self):
        state, meta, _, _ = self.start()
        for aliases in (['别名'], {'名': ['F001']}, {None: 'F001'}):
            result, _, _, issues = manage_state({}, state, meta, 2, 100, aliases=aliases)
            self.assertEqual(result['foreshadows'], state['foreshadows'])
            self.assertEqual(len(issues), 1)

    def test_budget_preserves_active_threads_and_archives_every_removed_entry(self):
        objects = {f'旧物{i}': {'location': '界' * 1000} for i in range(20)}
        objects['必需'] = {'location': '界' * 1000, 'owner': '甲'}
        state, meta, archive, issues = self.start(names=[f'伏笔{i}' for i in range(12)],
                                                  objects=objects, seen=['甲'])
        self.assertLessEqual(len(serialize_state(state)), MAX_STATE_CHARS)
        self.assertEqual(len(state['foreshadows']), MAX_ACTIVE)
        self.assertEqual(issues, [])
        self.assertEqual(len(state['objects']) + len(archive), len(objects))
        self.assertTrue(all(a['reason'] == 'context_budget' for a in archive))
        # Relevant ownership wins over lexical order, and recent facts beat old.
        state, meta, _, _ = manage_state({'objects': {'最近': '界' * 1000}}, state, meta, 2, 100, seen=['甲'])
        self.assertIn('最近', state['objects'])
        self.assertIn('必需', state['objects'])
        self.assertNotIn(' ', serialize_state(state))
        self.assertIn('界', serialize_state(state))
        self.assertEqual(json.loads(serialize_state(state)), state)

    def test_budget_exact_limit_and_single_oversized_record(self):
        base = compact_state({'objects': {'物': {'location': ''}}})
        base['objects'] = {'物': {'location': '界'}}
        room = MAX_STATE_CHARS - len(serialize_state(base)) + 1
        state, _, archive, _ = self.start(names=(), objects={'物': '界' * room})
        self.assertEqual(len(serialize_state(state)), MAX_STATE_CHARS)
        self.assertEqual(archive, [])
        state, _, archive, _ = self.start(names=(), objects={'物': '界' * (room + 1)})
        self.assertEqual(state['objects'], {})
        self.assertEqual(len(archive[0]['value']['location']), room + 1)

    def test_relationship_replacement_and_budget_removals_are_archived(self):
        state, meta, _, _ = self.start(relationships=['甲乙互信。', '甲丙不信。'])
        state, meta, archive, _ = manage_state({'relationships': ['甲乙互信。']}, state, meta, 2, 100)
        self.assertEqual(archive[0]['value'], '甲丙不信。')
        self.assertEqual(archive[0]['reason'], 'superseded')
        state, meta, archive, _ = manage_state({'relationships': ['长' * 13000]}, state, meta, 3, 100)
        self.assertEqual(state['relationships'], [])
        self.assertEqual([a['reason'] for a in archive], ['superseded', 'context_budget'])

    def test_legacy_snapshot_migration_and_closed_history_survive_empty_updates(self):
        previous = compact_state({'foreshadows': {'开着': 'open', '收过': 'closed'}})
        state, meta, archive, issues = manage_state({}, previous, {}, 5, 100)
        self.assertEqual(state['foreshadows'], {'F001': 'open'})
        self.assertEqual(meta['counts'], dict(active=1, closed_total=1, opened_total=2))
        self.assertEqual(archive[0]['key'], 'F002')
        state, meta, archive, issues = manage_state({}, state, meta, 6, 100)
        self.assertEqual(archive, [])
        self.assertEqual(meta['counts']['closed_total'], 1)

    def test_all_inputs_independent_and_deterministic(self):
        state, meta, _, _ = self.start(characters={'甲': '屋'})
        raw, seen, aliases, retired = {'foreshadows': {'寄信人': 'open'}}, ['甲'], {'寄信人': 'F001'}, []
        args = (raw, state, meta, 2, 100, seen, aliases)
        before = copy.deepcopy((args, retired))
        result = manage_state(*args, item_retired=retired)
        self.assertEqual((args, retired), before)
        self.assertEqual(result, manage_state(*args, item_retired=retired))
        result[1]['foreshadows']['F001']['aliases'].append('不泄漏')
        self.assertEqual((args, retired), before)

    def test_ordered_deltas_and_invalid_chapter_or_baseline(self):
        state, meta, archive, issues = manage_state([
            {'unresolved': ['线']}, {'characters': {'甲': '屋'}},
            {'objects': {'伞': '院'}}], {}, {}, 1, 100)
        self.assertEqual(state['foreshadows'], {'F001': 'open'})
        for chapter, total in ((0, 100), (101, 100), (1, 0), (True, 100), (1, 1.5)):
            with self.assertRaises(ValueError):
                manage_state({}, {}, {}, chapter, total)
        with self.assertRaises(ValueError):
            manage_state({}, state, dict(meta, chapter=2), 1, 100)
        with self.assertRaises(ValueError):
            manage_state({}, {'foreshadows': {f'线{i}': 'open' for i in range(13)}}, {}, 1, 100)

    def test_restored_absent_character_return_needs_no_entry_event(self):
        from bookengine.checks import check_events

        state, meta, _, _ = self.start(characters={'甲': {'location': '旧屋', 'present': False}})
        state, meta, archive, _ = manage_state({}, state, meta, 22, 100)
        indexed = {'characters': {'甲': archive[0]}}
        before = copy.deepcopy((state, indexed))
        check_state = restore_entities(state, indexed, ['甲'], before=23)
        text = '甲坐在新屋。'
        event = dict(kind='character', name='甲', location='新屋', action='stay',
                     paragraph=1, quote=text)
        self.assertEqual(check_events(state, [event], text), [])
        self.assertFalse(check_events(check_state, [event], text))
        self.assertEqual(check_events(check_state, [dict(event, action='enter')], text), [])
        self.assertEqual((state, indexed), before)

    def test_restore_uses_latest_eligible_snapshot_and_keeps_active_facts(self):
        state = compact_state({'characters': {'甲': '当前屋'}})
        archive = [dict(kind='characters', key='甲', value={'location': '旧屋'}, chapter=1),
                   dict(kind='characters', key='乙', value={'location': '第一屋'}, chapter=2),
                   dict(kind='characters', key='乙', value={'location': '第二屋'}, chapter=5),
                   dict(kind='characters', key='乙', value={'location': '未来屋'}, chapter=10),
                   dict(kind='foreshadows', key='F001', value='closed', chapter=2),
                   dict(kind='objects', key='伞', value={'location': '旧屋'}, chapter=4),
                   dict(kind='objects', key='未提及', value={'location': '屋'}, chapter=4)]
        result = restore_entities(state, archive, ['甲', '乙', '伞', 'F001'], before=10)
        self.assertEqual(result['characters']['甲']['location'], '当前屋')
        self.assertEqual(result['characters']['乙']['location'], '第二屋')
        self.assertEqual(result['objects'], {'伞': {'location': '旧屋', 'owner': ''}})
        self.assertEqual(result['foreshadows'], {})
        self.assertNotIn('乙', restore_entities(state, archive, ['乙'], before=2)['characters'])
        self.assertEqual(restore_entities(state, archive, []), state)

    def test_restored_objects_need_no_move_and_retirement_is_not_revoked(self):
        from bookengine.checks import check_events

        state, meta, _, _ = self.start(objects={'伞': {'location': '旧屋', 'owner': '甲'}})
        state, meta, archive, _ = manage_state({}, state, meta, 2, 100, item_retired=['伞'])
        check_state = restore_entities(state, archive, ['伞'], before=3)
        text = '伞放在新屋。'
        event = dict(kind='object', name='伞', owner='', location='新屋', action='stay',
                     paragraph=1, quote=text)
        self.assertFalse(check_events(check_state, [event], text))
        result, new_meta, _, _ = manage_state(check_state, state, meta, 3, 100)
        self.assertNotIn('伞', result['objects'])
        self.assertEqual(new_meta['retired_objects'], ['伞'])


if __name__ == '__main__':
    unittest.main()
