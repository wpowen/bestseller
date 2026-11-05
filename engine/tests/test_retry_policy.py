import unittest
from decimal import Decimal

from bookengine.retries import delay, next_network_wait


class RetryPolicyTests(unittest.TestCase):
    def test_delay_sequence_and_large_count(self):
        self.assertEqual([delay(n) for n in range(7)],
                         [10, 10, 60, 300, 600, 600, 600])
        self.assertEqual(delay(10 ** 1000), 600)

    def test_mixed_failures_wait_indefinitely(self):
        waited, waits, attempts = 0, [], 0
        errors = ['RemoteDisconnected', '429', 'timeout', 'connection', '503']
        for failures, error in enumerate(errors, 1):
            attempts += 1
            with self.subTest(error=error):
                planned = next_network_wait(failures, waited)
                if planned is None:
                    self.assertEqual(failures, 5)
                    break
                self.assertGreater(planned, 0)
                waits.append(planned)
                waited += planned  # Simulate completing the actual wait.
        self.assertEqual(waits, [10, 60, 300, 600, 600])
        self.assertEqual(waited, 1570)
        self.assertEqual(attempts, 5)

    def test_only_completed_waits_count(self):
        self.assertEqual(next_network_wait(100, 0), 600)
        self.assertEqual(next_network_wait(4, 899.5), 600)
        self.assertEqual(next_network_wait(5, 900), 600)
        self.assertEqual(next_network_wait(5, 970), 600)
        self.assertEqual(next_network_wait(1, 0), 10)

    def test_retry_after_respects_ten_minute_cap(self):
        for hint, expected in ((0, 10), (2, 10), (10.5, 10.5), (2000, 600)):
            with self.subTest(hint=hint):
                self.assertEqual(next_network_wait(1, 0, hint), expected)
        self.assertEqual(next_network_wait(4, 370, 1200), 600)
        self.assertEqual(next_network_wait(2, 2000, 3000), 600)

    def test_invalid_counts(self):
        bad = [-1, 0.5, 1.0, True, False, None, '1', float('nan'),
               float('inf'), -float('inf'), complex(1), Decimal(1)]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError):
                delay(value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                next_network_wait(value, 0)

    def test_invalid_seconds_even_when_already_exhausted(self):
        bad = [-1, -0.1, True, False, None, '1', float('nan'),
               float('inf'), -float('inf'), complex(1), Decimal(1)]
        for value in bad:
            with self.subTest(waited=value), self.assertRaises(ValueError):
                next_network_wait(1, value)
            for waited in (0, 900):
                with self.subTest(hint=value, waited=waited):
                    with self.assertRaises(ValueError):
                        next_network_wait(1, waited, value)


if __name__ == '__main__':
    unittest.main()
