"""Local, model-free regression tests for the isolated dialogue experiment."""
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import yaml


SPEC = importlib.util.spec_from_file_location(
    "dialogue_metrics", Path(__file__).parents[1] / "tools/dialogue_metrics.py"
)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def edit_for(row, text, **checks):
    return dict(id=row['id'], original=row['text'], text=text, **checks)


class DialogueExperimentTests(unittest.TestCase):
    def test_general_colon_introductions_and_text_label_exclusions(self):
        for prefix in ('同伴一把抱住他的肩', '后排声音终于响起',
                       '朋友吹声口哨', '来人眯起眼'):
            for colon in ('：', ':'):
                text = prefix + colon + '“快走。”'
                with self.subTest(text=text):
                    row = module.dialogues(text)[0]
                    self.assertTrue(module.spoken_dialogue(text, row))
                    self.assertEqual(row['speaker'], 'Unknown')
        for prefix in ('牌上写着', '墙上刻着', '它名叫', '所谓', '被称作',
                       '上面的字样是', '有这么几个字', '名称为', '屏幕显示',
                       '纸上注明', '标签内容为', '标题为'):
            text = prefix + '：“通行”'
            with self.subTest(text=text):
                self.assertFalse(module.spoken_dialogue(text, module.dialogues(text)[0]))
        for suffix in ('几个字', '三个字', '这个名称', '显示在屏幕上'):
            text = '“通行”' + suffix
            with self.subTest(text=text):
                self.assertFalse(module.spoken_dialogue(text, module.dialogues(text)[0]))
        text = '屏幕显示：“通行”。\n来人眯起眼：“快走。”'
        self.assertEqual([module.spoken_dialogue(text, row)
                          for row in module.dialogues(text)], [False, True])

    def test_spoken_dialogue_whitelist_preserves_labels_and_short_speech(self):
        cases = {
            '牌上写着“通行”': False,
            '牌上写着：“通行”': False,
            '石上刻着：“通行”': False,
            '此物名叫“通行”': False,
            '所谓“通行”只是个标签。': False,
            '他们称作“通行”': False,
            '“通行”字样很醒目。': False,
            '他看见“通行”字样。': False,
            '牌上写着\n“通行”': False,
            '“不。”': True,
            '  “不。”': True,
            '旁白。\n“不。”': True,
            '他说：“快走。”': True,
            '她问：“走吗？”': True,
            '他低声道：“快走。”': True,
            '他抬起头：“快走。”': True,
            '他想起“快走”这个词。': False,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                row = module.dialogues(text)[0]
                before = dict(row)
                self.assertIs(module.spoken_dialogue(text, row), expected)
                self.assertEqual(row, before)
                self.assertEqual(row['speaker'], 'Unknown')
        text = '“不。”'
        row = module.dialogues(text)[0]
        self.assertFalse(module.spoken_dialogue(text.replace('不', '好'), row))
        self.assertFalse(module.spoken_dialogue(text, {}))

    def test_parallel_internal_predicate_and_unrelated_clauses(self):
        for sentence in ('照片能排站位，伤口能排方向',
                         '账本能记收支，日历能记日程'):
            with self.subTest(sentence=sentence):
                self.assertTrue(module._parallel(sentence))
                self.assertEqual(module.metrics('“' + sentence + '。”')
                                 ['pattern_counts']['parallelism'], 1)
        for sentence in ('早上我去买菜，傍晚他才回家',
                         '桌上照片放好，墙边照片掉了'):
            with self.subTest(sentence=sentence):
                self.assertFalse(module._parallel(sentence))
                self.assertEqual(module.metrics('“' + sentence + '。”')
                                 ['pattern_counts']['parallelism'], 0)

    def test_metrics_load_actual_rules_from_yaml_on_import(self):
        # Change the data seen by a fresh import, without editing the shared file.
        path = Path(__file__).parents[1] / 'data/dialogue_experiment.yaml'
        data = yaml.safe_load(path.read_text(encoding='utf-8'))
        self.assertIn('rules', data)
        self.assertTrue({'见证', '真相', '证词'} <= set(data['rules']['abstract_nouns']))
        data['rules'].update(abstract_nouns=['苹果'], contrast_pattern='橘子',
                             aphorism_pattern='香蕉')
        configured = importlib.util.module_from_spec(SPEC)
        with patch.object(Path, 'read_text', return_value=yaml.safe_dump(data)):
            SPEC.loader.exec_module(configured)
        row = configured.metrics('“苹果橘子香蕉。”')
        self.assertEqual(row['abstract_noun_count'], 1)
        self.assertEqual(row['pattern_counts']['contrast'], 1)
        self.assertEqual(row['pattern_counts']['aphorism'], 1)
        old = configured.metrics('“不是命运，而是选择。人生就是选择。”')
        self.assertEqual(old['abstract_noun_count'], 0)
        self.assertEqual(old['pattern_counts']['contrast'], 0)
        self.assertEqual(old['pattern_counts']['aphorism'], 0)

    def test_offsets_quotes_and_unknown_speakers(self):
        text = '😀她想了想。“嗯。”\n他答："好！"又说：「走吧。」'
        rows = module.dialogues(text)
        self.assertEqual([r['text'] for r in rows], ['嗯。', '好！', '走吧。'])
        self.assertEqual(len({r['id'] for r in rows}), 3)
        self.assertEqual(rows, module.dialogues(text))
        for number, row in enumerate(rows, 1):
            self.assertEqual(row['id'], f"D{number}-{row['start']}-{row['end']}")
            self.assertEqual(text[row['start']:row['end']], row['text'])
            self.assertEqual(row['speaker'], 'Unknown')
            self.assertEqual(row['quote_start'], row['start'] - 1)
            self.assertEqual(row['quote_end'], row['end'] + 1)

    def test_unclosed_and_nested_quotes_are_conservative(self):
        self.assertEqual(module.dialogues('旁白“没说完'), [])
        text = '“他说「走吧」，我没动。”旁白。"好。"'
        rows = module.dialogues(text)
        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[0]['nested'])
        self.assertEqual(module.apply_dialogue_edits(
            text, [edit_for(rows[0], '走吧。')]), (text, []))

    def test_metrics_ignore_narration_and_are_json_safe(self):
        narration = '不是实力，是命运。你要坚持，你要努力，你要奋斗。人生就是选择。'
        text = narration + '“嗯。好吧。”'
        row = module.metrics(text)
        self.assertEqual(row['dialogue_chars'], 3)
        self.assertAlmostEqual(row['dialogue_ratio'], 3 / row['chars'])
        self.assertEqual(row['sentence_length_variance'], .25)
        self.assertEqual(row['abstract_noun_count'], 0)
        self.assertTrue(all(n == 0 for n in row['pattern_counts'].values()))
        self.assertEqual(json.loads(json.dumps(row, allow_nan=False)), row)

    def test_pattern_diagnostics_and_sentence_boundaries(self):
        row = module.metrics('“不是实力，是运气。不是命运，而是选择。'
                             '你要坚持，你要努力，你要奋斗。人生就是选择。”')
        self.assertEqual(row['pattern_counts']['contrast'], 2)
        self.assertEqual(row['pattern_counts']['parallelism'], 1)
        self.assertEqual(row['pattern_counts']['aphorism'], 1)
        self.assertGreater(row['abstract_noun_density'], 0)
        self.assertEqual(module.metrics('“不是这样。”旁白。“是那样。”')
                         ['pattern_counts']['contrast'], 0)
        self.assertEqual(module.metrics('“你往前走，我往后退。”')
                         ['pattern_counts']['parallelism'], 1)

    def test_long_sentence_threshold_and_empty_input(self):
        row = module.metrics('“' + '甲' * 35 + '。' + '乙' * 36 + '！”')
        self.assertEqual(row['pattern_counts']['long_sentence'], 1)
        self.assertEqual(row['sentence_length_variance'], .25)
        for text in ('', '\n', '123!?'):
            empty = module.metrics(text)
            self.assertEqual(empty['chars'], 0)
            self.assertEqual(empty['dialogue_ratio'], 0)
            self.assertEqual(empty['sentence_length_variance'], 0)

    def test_short_edits_preserve_every_narration_character(self):
        text = '  他停下。\n“嗯。”\n\n她笑："好。" 尾声。  '
        rows = module.dialogues(text)
        edits = [edit_for(rows[1], '行！'), edit_for(rows[0], '等等……')]
        before = [dict(e) for e in edits]
        result, accepted = module.apply_dialogue_edits(text, edits)
        self.assertEqual(result, '  他停下。\n“等等……”\n\n她笑："行！" 尾声。  ')
        self.assertEqual(len(accepted), 2)
        self.assertEqual(edits, before)
        json.dumps(accepted, ensure_ascii=False)
        self.assertEqual(module.apply_dialogue_edits(text, [
            {'start': rows[0]['start'], 'end': rows[0]['end'],
             'original': rows[0]['text'], 'text': '嗯'}
        ])[0], text.replace('嗯。', '嗯'))

    def test_blank_deletions_and_new_line_breaks_are_rejected(self):
        text = '旁白。“好。”结尾。'
        row = module.dialogues(text)[0]
        for replacement in ('', ' ', '\t\n', '好\n吧', '好\r吧',
                            '好\r\n吧', '好\u2028吧', '好\u2029吧'):
            with self.subTest(replacement=replacement):
                self.assertEqual(module.apply_dialogue_edits(
                    text, [edit_for(row, replacement)]), (text, []))
        result, accepted = module.apply_dialogue_edits(text, [edit_for(row, '嗯')])
        self.assertEqual(result, '旁白。“嗯”结尾。')
        self.assertEqual(len(accepted), 1)
        multiline = '“好\n吧。”'
        row = module.dialogues(multiline)[0]
        self.assertEqual(len(module.apply_dialogue_edits(
            multiline, [edit_for(row, '行\n吧。')])[1]), 1)

    def test_original_is_required_even_when_offsets_have_not_changed(self):
        text = '旁白。“好。”结尾。'
        row = module.dialogues(text)[0]
        self.assertEqual(module.apply_dialogue_edits(text, [
            {'id': row['id'], 'text': '嗯'}]), (text, []))
        changed = text.replace('好', '坏')
        self.assertEqual(module.apply_dialogue_edits(
            changed, [edit_for(row, '嗯')]), (changed, []))

    def test_required_abstract_words_and_overall_variance_proxy(self):
        row = module.metrics('见证真相证词。“见证，真相，证词。”')
        self.assertEqual(row['abstract_noun_count'], 3)
        self.assertEqual(row['abstract_noun_chars'], 6)
        self.assertEqual(row['abstract_noun_density'], 1)
        text = '“嗯。”甲乙都在场。“快点走吧。”'
        self.assertEqual(module.metrics(text)['sentence_length_variance'], 2.25)
        self.assertTrue(all(r['speaker'] == 'Unknown' for r in module.dialogues(text)))

    def test_unsafe_stale_and_duplicate_edits_are_rejected(self):
        text = '旁白。“好。”尾声。'
        row = module.dialogues(text)[0]
        for replacement in ('“坏”', '「坏」', '‘坏’', 'say', 'Ｓａｙ'):
            with self.subTest(replacement=replacement):
                self.assertEqual(module.apply_dialogue_edits(text, [
                    edit_for(row, replacement)]), (text, []))
        for edit in ({'start': 0, 'end': 2, 'text': '坏'},
                     edit_for(row, '坏', start=0),
                     {'id': row['id'], 'original': '错', 'text': '坏'},
                     {'id': row['id'], 'text': None}, None):
            self.assertEqual(module.apply_dialogue_edits(text, [edit]), (text, []))
        stale = '新' + text
        self.assertEqual(module.apply_dialogue_edits(stale, [
            edit_for(row, '坏')]), (stale, []))
        edit = edit_for(row, '行。')
        result, accepted = module.apply_dialogue_edits(text, [edit, edit])
        self.assertEqual(result, text.replace('好。', '行。'))
        self.assertEqual(len(accepted), 1)

    def test_existing_english_is_retained_but_not_added_or_repeated(self):
        text = '“OK，走吧。”'
        row = module.dialogues(text)[0]
        for replacement, count in [('OK，快走。', 1), ('快走。', 1),
                                   ('OK OK。', 0), ('okay。', 0)]:
            with self.subTest(replacement=replacement):
                _, accepted = module.apply_dialogue_edits(text, [
                    edit_for(row, replacement)])
                self.assertEqual(len(accepted), count)

    def test_straight_quote_replacement_cannot_escape_the_closing_delimiter(self):
        text = '旁白。"好。"随后离开。'
        row = module.dialogues(text)[0]
        self.assertEqual(module.apply_dialogue_edits(text, [
            edit_for(row, '走吧\\')]), (text, []))
        result, accepted = module.apply_dialogue_edits(text, [
            edit_for(row, '走吧\\\\')])
        self.assertEqual(len(accepted), 1)
        self.assertEqual(module.dialogues(result)[0]['text'], '走吧\\\\')


if __name__ == '__main__':
    unittest.main()
