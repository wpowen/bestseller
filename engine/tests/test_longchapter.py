import unittest

from bookengine.checks import check_events
from bookengine.longchapter import apply_fix, observations, state_result


class LongChapterTests(unittest.TestCase):
    def test_observations_only_three_hard_kinds_with_soft_text(self):
        text = '甲坐在新屋。\n\n命运的齿轮开始转动。'
        event = dict(kind='character', name='甲', location='新屋', action='stay', paragraph=1)
        previous = {'characters': {'甲': {'location': '旧屋', 'present': False}},
                    'objects': {}, 'clock': None}
        issues = observations(previous, [event], text, ['命运的齿轮'], [])
        self.assertEqual([i['kind'] for i in issues], ['banned_phrase'])
        self.assertFalse(any(i.get('hard') for i in issues))
        dead = observations(dict(previous, _dead=['甲']), [event], '甲坐在新屋。', [], [])
        self.assertEqual([i['kind'] for i in dead], ['dead_character'])

    def test_historical_items_without_owner_still_read_during_a_move(self):
        previous = {'characters': {'甲': {'location': '旧屋'}},
                    'objects': {'伞': {'location': '旧屋'}}}
        event = dict(kind='character', name='甲', location='院', action='move', paragraph=1)
        self.assertEqual(observations(previous, [event], '甲走到院里。', [], []), [])
        self.assertNotIn('owner', previous['objects']['伞'])

    def test_state_delta_can_update_only_an_object_and_preserves_characters(self):
        old = {'characters': {'甲': {'location': '屋', 'present': True}}}
        state, summary = state_result({'summary': '接过旧信。', 'objects': {'信': {'location': '甲手中'}}}, old)
        self.assertEqual(state['characters']['甲'], old['characters']['甲'])
        self.assertEqual(state['objects']['信']['location'], '甲手中')
        self.assertEqual(summary, '接过旧信。')

    def test_nested_state_summary_and_invalid_entity_type(self):
        _, summary = state_result({'state': {'summary': '稍作停留。', 'characters': {}}}, {})
        self.assertEqual(summary, '稍作停留。')
        with self.assertRaises(ValueError):
            state_result({'summary': '有摘要', 'characters': 3}, {})

    def test_existing_move_outside_edited_paragraph_can_resolve_departed_character(self):
        text = '甲走进屋。\n\n甲坐在桌旁。\n\n乙说话。'
        old = [{'kind': 'character', 'name': '甲', 'location': '屋', 'action': 'stay',
                'paragraph': 2, 'quote': '甲坐在桌旁'}]
        move = dict(old[0], action='enter', paragraph=1, quote='甲走进屋')
        bad_stay = dict(old[0], name='乙', location='未知远处', paragraph=3, quote='乙说话')
        fix = {'edits': [{'paragraph': 2, 'text': '甲坐在桌旁。'}], 'events': old + [move, bad_stay]}
        final, events = apply_fix(text, old, fix)
        previous = {'characters': {'甲': {'present': False, 'location': '院'}}}
        self.assertEqual(final, text)
        self.assertFalse(check_events(previous, events, final))
        self.assertNotIn(bad_stay, events)
        self.assertIn(move, events)

    def test_ungrounded_move_is_discarded_without_requiring_entry(self):
        text = '甲坐在桌旁。\n\n门关着。'
        event = {'kind': 'character', 'name': '甲', 'location': '屋', 'action': 'stay',
                 'paragraph': 1, 'quote': '甲坐在桌旁'}
        move = dict(event, action='enter', paragraph=2, quote='甲走进屋')
        final, events = apply_fix(text, [event], {'edits': [{'paragraph': 1, 'text': '甲坐在桌旁。'}],
                                                 'events': [event, move]})
        previous = {'characters': {'甲': {'present': False, 'location': '院'}}}
        self.assertFalse(check_events(previous, events, final))
        self.assertNotIn(move, events)
