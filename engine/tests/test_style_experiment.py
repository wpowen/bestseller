import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('style_experiment',Path(__file__).parents[1]/'tools/style_experiment.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class StyleMetricsTests(unittest.TestCase):
    def test_dialogue_attribution_is_one_sentence(self):
        self.assertEqual(module.metrics('“等等！”他喊。')['single_sentence_ratio'],1)

    def test_counts_actual_paragraphs_and_mental_actions(self):
        row=module.metrics('他担心迟到。可是又不愿走。\n\n“等等！”他喊。\n\n他把杯子放下。')
        self.assertEqual(row['paragraphs'],3)
        self.assertAlmostEqual(row['single_sentence_ratio'],2/3,places=4)
        self.assertEqual(row['mental_sentences'],2)
        self.assertEqual(row['placement_sentences'],1)

    def test_empty_text_has_no_invented_sentences(self):
        self.assertEqual(module.metrics('\n\n')['paragraphs'],0)
        self.assertEqual(module.metrics('\n\n')['chars'],0)
