import unittest

from bookengine.quality import anchor_events, normalize_patches, pointed_edits, validate_extract


class QualityTests(unittest.TestCase):
    def test_unique_quote_repairs_only_paragraph_number_without_mutation(self):
        value = {'events': [{'kind': 'character', 'paragraph': 99, 'quote': '乙来了'}]}
        fixed = anchor_events(value, '甲离开。\n\n乙来了。')
        self.assertEqual(fixed['events'][0]['paragraph'], 2)
        self.assertEqual(value['events'][0]['paragraph'], 99)

    def test_ambiguous_or_missing_quote_is_not_invented(self):
        value = {'events': [{'paragraph': 3, 'quote': '甲'}, {'paragraph': 8, 'quote': '乙'}]}
        self.assertEqual(anchor_events(value, '甲。\n\n甲。'), value)

    def test_extraction_structure(self):
        validate_extract({'summary': '回家', 'events': [], 'relationships': []})
        for value in ({}, {'summary': '', 'events': [], 'relationships': []},
                      {'summary': '回家', 'events': [None], 'relationships': []}):
            with self.assertRaises(ValueError):
                validate_extract(value)

    def test_only_pointed_edits_are_selected_without_changing_input(self):
        value = {'edits': [{'paragraph': 3, 'text': '修复'}, {'paragraph': 4, 'text': '额外修改'}]}
        self.assertEqual(pointed_edits(value, {3})['edits'], [{'paragraph': 3, 'text': '修复'}])
        self.assertEqual(len(value['edits']), 2)

    def test_state_field_aliases_preserve_supplied_values(self):
        value = {'events': [{'kind': 'object', 'quote': '杯子', 'paragraph': 1},
                            {'kind': 'foreshadow', 'action': 'closed', 'quote': '杯子', 'paragraph': 1},
                            {'kind': 'time', 'day': '1', 'minute': '900', 'quote': '杯子', 'paragraph': 1}]}
        events = anchor_events(value, '杯子在桌上。')['events']
        self.assertEqual(events[0]['owner'], '')
        self.assertEqual(events[1]['status'], 'closed')
        self.assertEqual((events[2]['day'], events[2]['minute']), (1, 900))

    def test_patch_event_missing_number_is_anchored_to_replacement(self):
        patch = {'chapter': 2, 'paragraph': 30, 'replacement': '甲走进小屋。',
                 'events': [{'kind': 'character', 'quote': '甲走进小屋', 'action': 'enter'}]}
        event = normalize_patches({'patches': [patch]})['patches'][0]['events'][0]
        self.assertEqual(event['paragraph'], 1)
        self.assertNotIn('paragraph', patch['events'][0])

    def test_incoming_phone_marks_only_the_named_remote_caller(self):
        quote = '林晚接到赵叔的电话'
        value = {'events': [{'kind': 'character', 'name': n, 'paragraph': 1, 'quote': quote}
                            for n in ('林晚', '赵叔')]}
        events = anchor_events(value, quote + '。')['events']
        self.assertNotIn('remote', events[0])
        self.assertTrue(events[1]['remote'])


if __name__ == '__main__':
    unittest.main()


class FinalReplayTests(unittest.TestCase):
    def test_deletion_remaps_surviving_and_replacement_events(self):
        from bookengine.quality import patched_records
        records = [{'summary': 'x', 'events': [{'paragraph': 1, 'name': '甲'},
                    {'paragraph': 3, 'name': '乙'}, {'paragraph': 5, 'name': '丙'}]}]
        patches = [{'chapter': 1, 'paragraph': 2, 'replacement': '', 'events': []},
                   {'chapter': 1, 'paragraph': 3, 'replacement': '新段', 'events': [{'name': '丁'}]}]
        updated = patched_records(records, patches)
        self.assertEqual([(e['name'], e['paragraph']) for e in updated[0]['events']],
                         [('甲', 1), ('乙', 2), ('丙', 4), ('丁', 2)])
        self.assertEqual(records[0]['events'][2]['paragraph'], 5)

    def test_fine_location_inside_room_is_compatible_but_outside_is_not(self):
        from bookengine.checks import same_place
        self.assertTrue(same_place('404室', '404室门内墙砖上'))
        self.assertFalse(same_place('404室', '404室门外'))
        self.assertFalse(same_place('404室窗边', '404室桌边'))
        self.assertTrue(same_place('赤脊旧凿壁', '赤脊旧凿壁营帐'))
        self.assertFalse(same_place('赤脊旧凿壁营帐门外', '赤脊旧凿壁营帐内'))

    def test_punctuation_change_preserves_events_without_location_alarm(self):
        from bookengine.quality import patched_records
        from bookengine.checks import check_events
        records = [{'events': [{'kind': 'character', 'name': '甲', 'location': '屋内',
                    'action': 'stay', 'paragraph': 1, 'quote': '甲仍在屋内'}]}]
        patches = [{'chapter': 1, 'paragraph': 1, 'replacement': '甲仍在屋内！', 'events': []}]
        updated = patched_records(records, patches)
        previous = {'characters': {'甲': {'present': True, 'location': '院内'}}}
        issues = check_events(previous, updated[0]['events'], '甲仍在屋内！')
        self.assertFalse(issues)
        self.assertEqual(updated[0]['events'][0]['quote'], '甲仍在屋内')
