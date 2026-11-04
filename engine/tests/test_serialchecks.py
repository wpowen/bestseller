import unittest
from bookengine.serialchapter import paragraph_flow, serial_observations
from bookengine.text import chinese_count


class SerialChecks(unittest.TestCase):
    def test_archived_character_return_never_blocks(self):
        archive = {'characters': {'甲': {'kind': 'characters', 'key': '甲', 'chapter': 22,
            'value': {'location': '旧屋', 'present': False}}}}
        event = dict(kind='character', name='甲', location='新屋', action='stay', paragraph=1, quote='甲坐在新屋')
        self.assertFalse(any(i.get('hard') for i in serial_observations({}, [event], '甲坐在新屋。', (), [], archive, before=30)))
        self.assertFalse(serial_observations({}, [dict(event, action='enter')], '甲坐在新屋。', (), [], archive, before=30))

    def test_retired_item_reappearance_never_blocks(self):
        event = dict(kind='object', name='断剑', location='屋', owner='', action='stay', paragraph=1, quote='断剑仍放在屋中')
        self.assertFalse(any(i.get('hard') for i in serial_observations({}, [event], '断剑仍放在屋中。', (), [], {}, ['断剑'])))

    def test_paragraph_grouping_never_changes_words_or_counts(self):
        text = ('甲抬头。\n\n“你终于到了。”\n\n屋外风吹动帘子。\n\n' * 12) + '---\n\n尾声。'
        grouped = paragraph_flow(text)
        self.assertEqual(''.join(text.split()), ''.join(grouped.split()))
        self.assertEqual(chinese_count(text), chinese_count(grouped))
        self.assertEqual(paragraph_flow(grouped), grouped)
