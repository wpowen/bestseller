import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from bookengine.length import correct_length
from bookengine.text import _paragraph_spans, chinese_count


class ShortLengthTests(unittest.TestCase):
    def correct(self, text, value, target=100, protected=(), minimal=False):
        client = SimpleNamespace(out=Path('/dev/null'), prefix='',minimal_prose=minimal)
        def stage(name, prompt, schema, **kwargs):
            result = kwargs['normalize'](value)
            if kwargs.get('validate'):
                kwargs['validate'](result)
            return result
        with patch.object(client, 'stage', create=True, side_effect=stage) as call:
            result = correct_length(client, 'length-test', text, {'events': ['交还信件']}, target, protected)
        self.assertEqual(call.call_count, 1)
        self.assertEqual(call.call_args.kwargs['max_attempts'], 1)
        return result, call.call_args.args

    def test_one_paragraph_expands_to_many_without_losing_story_beats(self):
        original = '甲交还信件。' + '甲' * 159
        paragraphs = [original + '乙' * 36] + ['丙' * 100] * 17
        final, args = self.correct(original, {'paragraphs': paragraphs}, 1900)
        self.assertEqual(chinese_count(original), 164)
        self.assertEqual(final, '\n\n'.join(paragraphs))
        self.assertEqual(len(_paragraph_spans(final)), 18)
        self.assertIn('甲交还信件。', final)
        self.assertIn('paragraphs', args[2])
        self.assertIn('保留全部情节点', args[1])

    def test_invalid_or_empty_expansion_keeps_original(self):
        original = '甲' * 16
        for value in ({}, None, [], {'paragraphs': []}, {'paragraphs': ['']},
                      {'paragraphs': ['乙' * 100, 3]}, {'paragraphs': '乙' * 100},
                      {'paragraphs': ['乙' * 10]}, {'paragraphs': ['乙' * 300]}):
            with self.subTest(value=value):
                self.assertEqual(self.correct(original, value)[0], original)

    def test_partial_expansion_is_kept_without_another_attempt(self):
        self.assertEqual(self.correct('甲' * 16, {'paragraphs': ['甲' * 30, '乙' * 30]})[0],
                         '甲' * 30 + '\n\n' + '乙' * 30)

    def test_slight_overrun_does_not_discard_a_complete_expansion(self):
        self.assertEqual(self.correct('甲'*16,{'paragraphs':['乙'*116]})[0],'乙'*116)

    def test_minimal_compression_selects_enough_edits_and_preserves_other_prose(self):
        paragraphs=['“你来吗？”他问。'+'乙'*500,'甲'*1000,'丙'*1000,'丁'*1000]
        result,args=self.correct('\n\n'.join(paragraphs),{'replacements':{'2':'甲'*200,'3':'丙'*500,'4':'丁'*300}},2200,minimal=True)
        self.assertTrue(1870<=chinese_count(result)<=2700)
        self.assertTrue(result.startswith(paragraphs[0]))
        self.assertTrue(result.endswith(paragraphs[3]))
        self.assertIn('replacements',args[2])
        self.assertIn('不将对白转述',args[1])

    def test_few_paragraphs_or_below_half_target_select_full_expansion(self):
        for text in ('甲' * 70, '\n\n'.join(['甲' * 10] * 4)):
            self.assertEqual(self.correct(text, {'paragraphs': ['乙' * 50, '丙' * 50]})[0],
                             '乙' * 50 + '\n\n' + '丙' * 50)

    def test_ordinary_expansion_keeps_source_number_selection(self):
        text = '\n\n'.join(['甲' * 20] * 3)
        final, args = self.correct(text, {'replacements': {'2': '乙' * 60}})
        self.assertEqual(final, '甲' * 20 + '\n\n' + '乙' * 60 + '\n\n' + '甲' * 20)
        self.assertIn('replacements', args[2])

    def test_empty_compression_result_keeps_original_exactly(self):
        text = '  ' + '甲' * 130 + '\n'
        self.assertEqual(self.correct(text, {})[0], text)

    def test_compression_still_selects_atomic_edits_by_source_number(self):
        text = '甲' * 12 + '\n\n' + '乙' * 12 + '\n\n' + '丙' * 6
        final, args = self.correct(text, {'replacements': {'1': '甲甲', '2': '乙乙乙', '99': '坏'},
                                          'paragraphs': ['错误']}, 20)
        self.assertEqual(final, '甲甲\n\n' + '乙' * 12 + '\n\n' + '丙' * 6)
        self.assertIn('replacements', args[2])

    def test_protected_paragraph_prevents_whole_chapter_expansion(self):
        text = '甲' * 10 + '\n\n' + '乙' * 10
        final, args = self.correct(text, {'paragraphs': ['丙' * 100],
                                         'replacements': {'1': '坏' * 90, '2': '乙' * 90}}, protected={1})
        self.assertEqual(final, '甲' * 10 + '\n\n' + '乙' * 90)
        self.assertIn('replacements', args[2])
