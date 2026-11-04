import unittest

from bookengine.client import retry_after
from bookengine.runner import validate_bible, validate_review, validate_state
from bookengine.text import normalize_review


class CheckTests(unittest.TestCase):
    def test_observed_review_field_typo(self):
        original = {'issues': [{'paragraph': 1, 'suggesting': '保留意见原文'}]}
        repaired = normalize_review(original)
        self.assertEqual(repaired, {'issues': [{'paragraph': 1, 'suggestion': '保留意见原文'}]})
        self.assertIn('suggesting', original['issues'][0])
        validate_review(repaired, '甲')

    def test_review_requires_real_paragraph(self):
        validate_review({'issues': [{'paragraph': 2, 'suggestion': '改清楚指代'}]}, '甲\n\n乙')
        for issue in (None, {'paragraph': True, 'suggestion': '改'},
                      {'paragraph': 3, 'suggestion': '改'}, {'paragraph': 1, 'suggestion': ''}):
            with self.subTest(issue=issue), self.assertRaises(ValueError):
                validate_review({'issues': [issue]}, '甲\n\n乙')

    def test_bible_and_state_fields(self):
        bible = {'characters': [{'name': '甲', 'wants': '陪伴', 'speech': '短句'}], 'places': ['家']}
        validate_bible(bible)
        for character in (None, {'name': '甲', 'speech': '短句'}):
            with self.subTest(character=character), self.assertRaises(ValueError):
                validate_bible(dict(bible, characters=[character]))
        state = dict(summary='甲回家', who_is_where=[], objects=[], time='当天晚上',
                     relationships=[], unresolved=[])
        validate_state(state)
        with self.assertRaises(ValueError):
            validate_state(dict(state, summary=''))
        with self.assertRaises(ValueError):
            validate_state(dict(state, objects='伞'))

    def test_retry_after_numeric_and_date(self):
        self.assertEqual(retry_after('120'), 120)
        self.assertEqual(retry_after('Fri, 01 Jan 2100 00:00:00 GMT') > 60, True)
        self.assertEqual(retry_after('Wed, 01 Jan 2020 00:00:00 GMT'), 0)
        self.assertEqual(retry_after('bad date'), 0)


if __name__ == '__main__':
    unittest.main()
