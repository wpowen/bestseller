import unittest
from bookengine.longform import resume_stop


class ResumeStopTests(unittest.TestCase):
    def test_long_speed_boundary_and_short_books_are_not_long_speed_samples(self):
        self.assertFalse(resume_stop(49, 9999, 0, 100, 300))
        self.assertFalse(resume_stop(50, 4500, 0, 100, 300))
        self.assertTrue(resume_stop(50, 4500.01, 0, 100, 300))
        self.assertTrue(resume_stop(51, 4501, 0, 100, 300))
        self.assertFalse(resume_stop(1, 120, 0, 3, 10))

    def test_failure_rate_applies_to_both_and_zero_or_later_chapters_do_not_stop(self):
        self.assertFalse(resume_stop(10, 600, 1, 10, 300))
        self.assertTrue(resume_stop(50, 2000, 11, 100, 300))
        self.assertFalse(resume_stop(0, 999, 3, 3, 300))
        self.assertTrue(resume_stop(100, 4501, 0, 100, 300))
