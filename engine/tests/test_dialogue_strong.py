"""Offline checks for substantive dialogue edits; no engine, DB or model calls."""
import json
from pathlib import Path
import unittest

import yaml

from tools.dialogue_diff import dialogue_diff
from tools.dialogue_metrics import apply_dialogue_edits, dialogues


class DialogueStrongTests(unittest.TestCase):
    def test_punctuation_whitespace_and_particles_do_not_count(self):
        before = '他说：“你把门关上。”\n“快走。”'
        after = '他说：“你呀，把门关上吧！”\n“ 快走啊！ ”'
        result = dialogue_diff(before, after)
        self.assertEqual(result['total_lines'], 2)
        self.assertEqual(result['changed_lines'], 0)
        self.assertEqual(result['substantive_ratio'], 0.0)
        self.assertEqual(result['mean_edit_distance'], 0.0)
        self.assertGreater(result['mean_literal_edit_distance'], 0.0)
        self.assertTrue(result['narration_unchanged'])

    def test_real_rewrite_and_unchanged_lines_share_the_denominator(self):
        before = '他说：“把门关上，外头冷。”\n她问：“还走吗？”'
        after = '他说：“冻死我了，门赶紧关严！”\n她问：“还走吗？”'
        result = dialogue_diff(before, after)
        self.assertEqual(result['total_lines'], 2)
        self.assertEqual(result['changed_lines'], 1)
        self.assertEqual(result['substantive_ratio'], 0.5)
        self.assertEqual(result['mean_edit_distance'],
                         result['lines'][0]['edit_distance'] / 2)
        self.assertTrue(result['narration_unchanged'])
        self.assertTrue(result['slot_alignment_unchanged'])
        self.assertEqual(result, dialogue_diff(before, after))
        json.dumps(result, ensure_ascii=False, allow_nan=False)

    def test_narration_and_quoted_labels_are_protected(self):
        before = '牌上写着：“通行”。\n他说：“快走。”尾声。'
        rewritten = before.replace('快走', '赶紧动身')
        result = dialogue_diff(before, rewritten)
        self.assertEqual(result['total_lines'], 1)
        self.assertEqual(result['changed_lines'], 1)
        self.assertTrue(result['narration_unchanged'])
        for changed in (rewritten.replace('尾声', '终场'),
                        rewritten.replace('通行', '禁止')):
            with self.subTest(changed=changed):
                self.assertFalse(dialogue_diff(before, changed)['narration_unchanged'])

    def test_short_lines_need_two_content_han_edits_and_distance_threshold(self):
        for old, new, expected in [('走吧', '赶紧动身', 1),
                                   ('好', '行', 0),
                                   ('你去', '他去', 0),
                                   ('你去', '他来', 1),
                                   ('甲乙丙丁戊己庚辛', '壬癸丙丁戊己庚辛', 1),
                                   ('甲乙丙丁戊己庚辛壬', '癸子丙丁戊己庚辛壬', 0)]:
            with self.subTest(old=old, new=new):
                result = dialogue_diff(f'“{old}”', f'“{new}”')
                self.assertEqual(result['changed_lines'], expected)
                for key in ('mean_edit_distance', 'mean_literal_edit_distance'):
                    self.assertGreaterEqual(result[key], 0.0)
                    self.assertLessEqual(result[key], 1.0)

    def test_fact_changes_warn_even_below_substantive_threshold(self):
        before = '他说：“明天只有付满20元才放行，不许赊账。”'
        for after, kind in [(before.replace('20', '30'), 'numbers_changed'),
                            (before.replace('不许', '允许'), 'negation_changed'),
                            (before.replace('明天', '后天'), 'time_changed'),
                            (before.replace('只有', '只要'), 'condition_changed')]:
            with self.subTest(kind=kind):
                result = dialogue_diff(before, after)
                self.assertIn(kind, [flag['kind'] for flag in result['facts_program_flags']])
        self.assertEqual(dialogue_diff(before, before.replace('20', '30'))['changed_lines'], 0)
        self.assertIn('numbers_changed', [flag['kind'] for flag in
                      dialogue_diff('“给我三百元。”', '“给我五百元。”')['facts_program_flags']])

    def test_slots_align_by_order_including_labels_not_shifted_offsets(self):
        before = '“走吧。”\n牌上写着“通行”。\n“等我。”'
        rows = dialogues(before)
        after, accepted = apply_dialogue_edits(before, [
            dict(id=rows[0]['id'], original=rows[0]['text'], text='赶紧动身。'),
            dict(id=rows[2]['id'], original=rows[2]['text'], text='慢点，带上我。')])
        result = dialogue_diff(before, after)
        self.assertEqual(len(accepted), 2)
        self.assertEqual(result['changed_lines'], 2)
        self.assertEqual([line['slot'] for line in result['lines']], [1, 3])
        self.assertTrue(result['narration_unchanged'])
        for invalid in (after + '\n“新增发言。”',
                        '“赶紧动身。”\n牌上写着“通行”。'):
            with self.subTest(invalid=invalid):
                bad = dialogue_diff(before, invalid)
                self.assertFalse(bad['slot_alignment_unchanged'])
                self.assertEqual(bad['changed_lines'], 0)
                self.assertTrue(bad['facts_program_flags'])

    def test_empty_or_label_only_input_has_finite_zero_metrics(self):
        for text in ('', '没有对白。', '牌上写着“通行”。'):
            result = dialogue_diff(text, text)
            self.assertEqual(result['total_lines'], 0)
            self.assertEqual(result['substantive_ratio'], 0.0)
            self.assertEqual(result['mean_edit_distance'], 0.0)
            self.assertEqual(result['mean_literal_edit_distance'], 0.0)
            self.assertTrue(result['narration_unchanged'])
            self.assertEqual(result['facts_program_flags'], [])

    def test_strong_prompt_has_targets_and_compatible_edit_contract(self):
        data = yaml.safe_load((Path(__file__).parents[1] /
                               'data/dialogue_strong.yaml').read_text(encoding='utf-8'))
        self.assertGreaterEqual(data['target_substantive_ratio'], 0.6)
        self.assertEqual(data['acceptance_substantive_ratio'], 0.4)
        self.assertIn('polish_prompt', data)
        # The caller supplies original before using the existing edit function.
        source = '“快走。”'
        row = dialogues(source)[0]
        edit = {'id': row['id'], 'text': '赶紧动身。'}
        edit['original'] = row['text']
        final, accepted = apply_dialogue_edits(source, [edit])
        self.assertEqual(len(accepted), 1)
        self.assertEqual(dialogue_diff(source, final)['changed_lines'], 1)


if __name__ == '__main__':
    unittest.main()
