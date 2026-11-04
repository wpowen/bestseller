"""Pure local regressions for serial checks; no models, data fixtures, or books."""
import unittest
from unittest.mock import patch
from bookengine.serial_checks import adjacent, catch_hits


class SerialChecksTests(unittest.TestCase):
    def setUp(self):
        for target in ('socket.create_connection', 'socket.socket.connect', 'urllib.request.urlopen'):
            guard = patch(target, side_effect=AssertionError('Network forbidden'))
            guard.start()
            self.addCleanup(guard.stop)
        self.cards = [dict(name='林宁', catchphrase='先走。'), dict(name='周青')]

    def kinds(self, text, history=(), cards=None):
        return {kind for hit in catch_hits(text, history, self.cards if cards is None else cards)
                for kind in hit['categories']}

    def test_adjacent_removes_short_duplicate_lines_across_blank_lines_only(self):
        text = '走。\r\n\r\n走。\r\n等。\n走。\n走。'
        self.assertEqual(adjacent(text), '走。\r\n\r\n等。\n走。\n')
        self.assertEqual(adjacent('甲\n甲\n\n甲\n乙'), '甲\n\n乙')
        self.assertEqual(adjacent(' \r\n\t\n'), ' \r\n\t\n')
        self.assertEqual(adjacent(''), '')

    def test_adjacent_keeps_other_whitespace_characters_and_nonadjacent_repetition(self):
        text = ' 甲。\n甲。 \n\t\n乙。\n 甲。\n'
        self.assertEqual(adjacent(text), ' 甲。\n \n\t\n乙。\n 甲。\n')
        self.assertEqual(adjacent('甲\u2028甲\u2028乙'), '甲\u2028乙')

    def test_adjacent_removes_actual_cross_paragraph_inline_and_short_duplicates(self):
        for text, expected in (
            ('守夜人忽然割开一束。\n\n守夜人忽然割开一束。七封信落在地上。',
             '守夜人忽然割开一束。\n\n七封信落在地上。'),
            ('沈迟以左腕压住黑水。沈迟以左腕压住黑水。', '沈迟以左腕压住黑水。'),
            ('沈迟没答。沈迟没答。', '沈迟没答。'),
            ('沈迟没答。沈迟没答。沈迟没答。许沅退开。', '沈迟没答。许沅退开。'),
            ('  沈迟没答。\r\n\r\n\t沈迟没答。许沅退开。  ', '  沈迟没答。\r\n\r\n\t许沅退开。  '),
            ('他敲门。她开门。\n\n他敲门。他敲门。她开门。', '他敲门。她开门。\n\n'),
        ):
            with self.subTest(text=text):
                self.assertEqual(adjacent(text), expected)
                self.assertEqual(adjacent(expected), expected)

    def test_adjacent_still_removes_whole_multisentence_lines_and_preserves_gaps(self):
        for newline in ('\n', '\r\n', '\r', '\v', '\f', '\x1c', '\x1d', '\x1e', '\x85', '\u2028', '\u2029'):
            with self.subTest(newline=repr(newline)):
                line = '沈迟推门。许沅进屋。'
                self.assertEqual(adjacent(line + newline * 2 + line + newline + '灯亮了。'),
                                 line + newline * 2 + '灯亮了。')
        self.assertEqual(adjacent('甲\r\n\t\r\n甲\n乙'), '甲\r\n\t\r\n乙')

    def test_adjacent_preserves_distinct_speakers_responses_and_unmatched_characters(self):
        for text in ('沈迟没答。许沅没答。', '沈迟没答。沈迟没答！',
                     '沈迟说：“好。”许沅说：“好。”', '“好。”沈迟说。\n\n“好。”许沅问。',
                     '“好。”“好。”许沅说。', '「好。」\n\n「好。」许沅问。',
                     '沈迟没答。许沅退开。沈迟没答。', '甲　甲'):
            with self.subTest(text=text):
                self.assertEqual(adjacent(text), text)

    def test_repeat_quote_uses_complete_units_original_ids_and_compact_fields(self):
        old = '林宁说：“这扇铁门后面一直没有半点动静。”'
        text = '门开了。\n\n周青说：「这扇铁门后面，一直没有半点动静！」'
        hits = catch_hits(text, [old], self.cards)
        self.assertEqual(hits, [dict(id=2, categories=['repeat_quote'])])
        self.assertTrue(all(set(hit) == {'id', 'categories'} for hit in hits))

    def test_whole_quote_crossing_sentences_marks_only_its_original_sentence_ids(self):
        quote = '“先把大门关好。然后一起回家去。”'
        self.assertEqual(catch_hits('门开了。' + quote, [quote], []),
                         [dict(id=2, categories=['repeat_quote']), dict(id=3, categories=['repeat_quote'])])

    def test_repetition_needs_ten_han_characters_exact_units_and_last_nine_chapters(self):
        old = '这扇铁门后面一直没有半点动静。'
        self.assertNotIn('repeat_quote', self.kinds(old, [old] + ['别的话。'] * 9))
        self.assertIn('repeat_quote', self.kinds(old, [old] + ['别的话。'] * 8))
        self.assertNotIn('repeat_quote', self.kinds('“先走。”', ['“先走。”']))
        self.assertNotIn('repeat_quote', self.kinds('这扇铁门后面一直没有半点动静，他又等了很久。', [old]))
        self.assertNotIn('repeat_quote', self.kinds('“这扇铁门后面一直没有半点动静，还得继续等。”', [old]))

    def test_explicit_wrong_speaker_is_owner_candidate_but_correct_owner_is_not(self):
        self.assertIn('catchphrase_owner', self.kinds('周青说道：“先走！”'))
        self.assertNotIn('catchphrase_owner', self.kinds('林宁答道：「先走。」'))
        self.assertNotIn('catchphrase_owner', self.kinds('周青说：“你先走吧。”'))

    def test_long_catchphrase_can_be_contained_but_short_one_must_match_exactly(self):
        cards = [dict(name='林宁', catchphrase=['我这条命只认自己选的路']), dict(name='周青')]
        self.assertIn('catchphrase_owner', self.kinds('周青说：“记住，我这条命只认自己选的路，别拦我。”', cards=cards))
        self.assertNotIn('catchphrase_owner', self.kinds('林宁说：“记住，我这条命只认自己选的路。”', cards=cards))

    def test_uncertain_ownership_other_paragraphs_and_reported_speech_are_skipped(self):
        for text in ('林宁望着周青说：“先走。”', '周青站在门口。“先走。”',
                     '周青\n\n说道：“先走。”', '陌生人说：“先走。”',
                     '林宁说：“他转述周青说：「先走。」”'):
            with self.subTest(text=text):
                self.assertNotIn('catchphrase_owner', self.kinds(text))

    def test_quota_requires_own_explicit_history_within_last_nine_chapters(self):
        old = '林宁说：“先走。”'
        current = '门开了。林宁突然开口：“先走！”'
        self.assertEqual(catch_hits(current, [old], self.cards),
                         [dict(id=2, categories=['catchphrase_quota'])])
        self.assertNotIn('catchphrase_quota', self.kinds(current, ['周青说：“先走。”']))
        self.assertNotIn('catchphrase_quota', self.kinds(current, ['林宁记得先走这两个字。']))
        self.assertNotIn('catchphrase_quota', self.kinds(current, [old] + ['无关。'] * 9))
        self.assertIn('catchphrase_quota', self.kinds(current, [old] + ['无关。'] * 8))

    def test_shared_catchphrase_is_not_misattributed_and_inputs_stay_unchanged(self):
        cards = [dict(name='林宁', catchphrase='先走'), dict(name='周青', catchphrase='先走')]
        history = ['林宁说：“先走。”']
        self.assertNotIn('catchphrase_owner', self.kinds('周青说：“先走。”', history, cards))
        self.assertEqual(cards, [dict(name='林宁', catchphrase='先走'), dict(name='周青', catchphrase='先走')])
        self.assertEqual(history, ['林宁说：“先走。”'])
        self.assertEqual(catch_hits('', history, cards), [])

    def test_postposed_explicit_speaker_marks_the_quote_sentence_only(self):
        phrase = '先别解释，告诉我现在会死谁。'
        cards = [dict(name='林宁', catchphrase=phrase), dict(name='许沅')]
        for verb in ('说', '道', '问', '说道', '问道', '答道'):
            with self.subTest(verb=verb):
                text = f'门开了。“{phrase}”许沅{verb}。'
                self.assertEqual(catch_hits(text, [], cards),
                    [dict(id=2, categories=['catchphrase_owner'])])
        self.assertNotIn('catchphrase_owner', self.kinds(f'“{phrase}”林宁说。', cards=cards))

    def test_postposed_owner_quota_uses_explicit_history_in_either_order(self):
        current = '“先走！”林宁说。'
        for old in ('林宁说：“先走。”', '“先走。”林宁问。'):
            with self.subTest(old=old):
                self.assertEqual(catch_hits(current, [old], self.cards),
                    [dict(id=1, categories=['catchphrase_quota'])])
        self.assertNotIn('catchphrase_quota', self.kinds(current, ['“先走。”周青说。']))

    def test_postposed_ownership_does_not_guess_from_nearby_names(self):
        for text in ('“先走。”周青站在门口。', '“先走。”林宁看着周青说。',
                     '“先走。”\n\n周青说。', '“先走。”陌生人说。',
                     '“先走。”周青说不清楚。', '林宁说：“他转述道：「先走。」周青说。”'):
            with self.subTest(text=text):
                self.assertNotIn('catchphrase_owner', self.kinds(text))
