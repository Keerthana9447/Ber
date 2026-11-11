import unittest

from app.resilience import MAX_RETRY_DELAY_SECONDS, retry_delay_seconds, should_log_traceback


class RetryDelayTests(unittest.TestCase):
    def test_normal_interval_after_success(self):
        self.assertEqual(retry_delay_seconds(60, 0), 60)

    def test_exponential_backoff_with_cap(self):
        self.assertEqual([retry_delay_seconds(60, n) for n in (1, 2, 3, 4)], [60, 120, 240, 480])
        self.assertEqual(retry_delay_seconds(60, 5), MAX_RETRY_DELAY_SECONDS)
        self.assertEqual(retry_delay_seconds(60, 500), MAX_RETRY_DELAY_SECONDS)

    def test_cap_never_below_the_base_interval(self):
        self.assertEqual(retry_delay_seconds(3600, 3), 3600)

    def test_invalid_base_rejected(self):
        with self.assertRaises(ValueError):
            retry_delay_seconds(0, 1)

    def test_tracebacks_logged_first_and_every_tenth_failure(self):
        self.assertTrue(should_log_traceback(1))
        self.assertFalse(should_log_traceback(2))
        self.assertFalse(should_log_traceback(9))
        self.assertTrue(should_log_traceback(10))
        self.assertTrue(should_log_traceback(20))


if __name__ == "__main__":
    unittest.main()
