import unittest

from bookengine.length import choose_edits, normalize_candidates
from bookengine.text import chinese_count


class LengthTests(unittest.TestCase):
    def test_prefixed_and_root_paragraph_maps_are_recovered(self):
        text='甲甲。\n\n乙乙。'
        for value in ({'replacements':{'P1':'甲'}},{'P1':'甲'}, {'edits':[{'id':'[P1]','text':'甲'}]}):
            self.assertEqual(normalize_candidates(value,text)['edits'],[{'paragraph':1,'text':'甲'}])
        self.assertEqual(normalize_candidates({'optional_deletions':['p2']},text)['edits'],[{'paragraph':2,'text':''}])

    def test_compression_selects_needed_independent_edits_and_keeps_other_paragraphs(self):
        text = '甲' * 12 + '\n\n' + '乙' * 12 + '\n\n' + '丙' * 6
        final, edits = choose_edits(text, [{'paragraph': 1, 'text': '甲' * 2},
                                          {'paragraph': 2, 'text': '乙' * 3}], 20)
        self.assertEqual(chinese_count(final), 20)
        self.assertEqual([e['paragraph'] for e in edits], [1])
        self.assertIn('乙' * 12, final)
        self.assertIn('丙' * 6, final)

    def test_expansion_does_not_take_oversized_candidate(self):
        final, edits = choose_edits('甲甲。\n\n乙乙。',
                [{'paragraph': 1, 'text': '甲' * 18}, {'paragraph': 2, 'text': '乙' * 30}], 20)
        self.assertEqual(chinese_count(final), 20)
        self.assertEqual([e['paragraph'] for e in edits], [1])

    def test_deletion_and_single_paragraph_replacement_are_supported(self):
        final, _ = choose_edits('甲' * 20 + '\n\n' + '乙' * 10,
                               [{'paragraph': 2, 'text': ''}], 20)
        self.assertEqual(final, '甲' * 20)
        final, _ = choose_edits('甲' * 30, [{'paragraph': 1, 'text': '甲' * 20}], 20)
        self.assertEqual(chinese_count(final), 20)

    def test_insufficient_proposal_is_accepted_without_fabricating_or_truncating_text(self):
        text = '甲' * 40 + '\n\n乙'
        final, edits = choose_edits(text, [{'paragraph': 2, 'text': ''}], 20)
        self.assertEqual(chinese_count(final), 40)
        self.assertEqual(edits, [{'paragraph': 2, 'text': ''}])

    def test_normalization_filters_duplicates_and_invalid_ids(self):
        value = normalize_candidates({'patches': [{'paragraph': '1', 'replacement': ''},
                 {'paragraph': 1, 'text': '重复'}, {'paragraph': 8, 'text': '无效'}]}, '甲。\n\n乙。')
        self.assertEqual(value['edits'], [{'paragraph': 1, 'text': ''}])
        self.assertEqual(normalize_candidates(value, '甲。\n\n乙。', {1})['edits'], [])
        self.assertEqual(normalize_candidates({'replacements': {'2': '乙'}}, '甲。\n\n乙乙。')['edits'],
                         [{'paragraph': 2, 'text': '乙'}])
        value = {'replacements': {'1': '甲', '2': '乙'}, 'optional_deletions': ['1', 2, 99]}
        self.assertEqual(normalize_candidates(value, '甲甲。\n\n乙乙。', {2})['edits'],
                         [{'paragraph': 1, 'text': ''}])
