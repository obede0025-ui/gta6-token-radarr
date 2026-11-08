import unittest
from unittest.mock import Mock
import requests
from v63_quote_monitor import QuotePacer

class QuotePacerTests(unittest.TestCase):
    def test_retry_429_then_success(self):
        clock = [0.]
        pauses = []
        def sleep(t):
            pauses.append(t)
            clock[0] += t
        response = requests.Response()
        response.status_code = 429
        response.headers["Retry-After"] = "3"
        err = requests.HTTPError("rate limited", response=response)
        fn = Mock(side_effect=[err, {"ok": True}])
        p = QuotePacer(clock=lambda: clock[0], sleep=sleep)
        self.assertEqual(p.call(fn), {"ok": True})
        self.assertEqual(fn.call_count, 2)
        self.assertGreaterEqual(sum(pauses), 3)
    def test_no_retry_for_other_http_error(self):
        response = requests.Response()
        response.status_code = 403
        err = requests.HTTPError("forbidden", response=response)
        fn = Mock(side_effect=err)
        with self.assertRaises(requests.HTTPError):
            QuotePacer(clock=lambda: 0., sleep=lambda _: None).call(fn)
        self.assertEqual(fn.call_count, 1)
    def test_paces_multiple_calls(self):
        clock = [0.]
        pauses = []
        def sleep(t):
            pauses.append(t)
            clock[0] += t
        p = QuotePacer(min_interval=1.5, clock=lambda: clock[0], sleep=sleep)
        p.call(lambda: True)
        p.call(lambda: True)
        self.assertGreaterEqual(sum(pauses), 1.5)
    def test_bounded_retries(self):
        response = requests.Response()
        response.status_code = 429
        error = requests.HTTPError("rate limited", response=response)
        fn = Mock(side_effect=error)
        clock = [0.]
        def sleep(t):
            clock[0] += t
        p = QuotePacer(retries=2, clock=lambda: clock[0], sleep=sleep)
        with self.assertRaises(requests.HTTPError):
            p.call(fn)
        self.assertEqual(fn.call_count, 3)

if __name__ == "__main__":
    unittest.main()
