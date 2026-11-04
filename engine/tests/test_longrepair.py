import copy
import unittest

from bookengine.longrepair import apply_calibration
from bookengine.quality import anchor_events


class LongRepairTests(unittest.TestCase):
    def test_object_appearance_is_a_location_change_not_character_entrance(self):
        event = {'kind': 'object', 'action': 'enter', 'name': '旧契', 'location': '台上',
                 'paragraph': 1, 'quote': '旧契从台上升起'}
        self.assertEqual(anchor_events({'events': [event]}, '旧契从台上升起。')['events'][0]['action'], 'move')
        self.assertEqual(event['action'], 'enter')

    def setUp(self):
        self.case = {'body': '甲走进屋。\n\n乙抬头。', 'before': {}, 'events': [
            {'kind': 'character', 'name': '甲', 'location': '旧称', 'action': 'enter',
             'paragraph': 1, 'quote': '甲走进屋'}], 'issues': [{'paragraph': 1}]}

    def test_metadata_only_correction_preserves_body_and_original_observation(self):
        original = copy.deepcopy(self.case)
        text, events, rejected = apply_calibration(self.case,
                {'edits': [], 'event_updates': [{'index': 0, 'fields': {'location': '屋'}}]})
        self.assertEqual(text, self.case['body'])
        self.assertEqual(events[0]['location'], '屋')
        self.assertEqual(rejected, [])
        self.assertEqual(self.case, original)

    def test_cannot_edit_unpointed_prose_or_accept_a_fabricated_quote(self):
        for value in ({'edits': [{'paragraph': 2, 'text': '不许改'}]},
                      {'event_updates': [{'index': 0, 'fields': {'quote': '甲正在另一城'}}]},
                      {'rejects': [{'index': 0}]},
                      {'rejects': [{'index': 0, 'reason': '不能因问题不想改就删掉真实入场'}]}):
            with self.assertRaises(ValueError):
                apply_calibration(self.case, value)
