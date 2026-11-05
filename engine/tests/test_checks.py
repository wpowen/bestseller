import unittest

from bookengine.client import retry_after
from bookengine.text import normalize_review


class CheckTests(unittest.TestCase):
    def test_observed_review_field_typo(self):
        original = {'issues': [{'paragraph': 1, 'suggesting': '保留意见原文'}]}
        repaired = normalize_review(original)
        self.assertEqual(repaired, {'issues': [{'paragraph': 1, 'suggestion': '保留意见原文'}]})
        self.assertIn('suggesting', original['issues'][0])

    def test_retry_after_numeric_and_date(self):
        self.assertEqual(retry_after('120'), 120)
        self.assertEqual(retry_after('Fri, 01 Jan 2100 00:00:00 GMT') > 60, True)
        self.assertEqual(retry_after('Wed, 01 Jan 2020 00:00:00 GMT'), 0)
        self.assertEqual(retry_after('bad date'), 0)


if __name__ == '__main__':
    unittest.main()
