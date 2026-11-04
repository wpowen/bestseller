import unittest
from bookengine.longutil import loose_object


class PartialOutputTests(unittest.TestCase):
    def test_complete_array_is_usable(self):
        self.assertEqual(loose_object('["一句。","另句。"]'),['一句。','另句。'])

    def test_broken_field_does_not_discard_later_good_fields(self):
        value=loose_object('{"state":{"characters":{}}},"scene_types":["action"],"actual_type":"对抗"}')
        self.assertEqual(value['scene_types'],['action'])
        self.assertEqual(value['actual_type'],'对抗')
        self.assertTrue(value.recovered_output)

    def test_truncated_candidates_keep_only_finished_strings(self):
        value=loose_object('{"replacements":{"1":"完整句。","2":"未完成')
        self.assertEqual(value['replacements'],{'1':'完整句。'})

    def test_unusable_text_still_fails(self):
        with self.assertRaises(ValueError):loose_object('坏的格式: [')
