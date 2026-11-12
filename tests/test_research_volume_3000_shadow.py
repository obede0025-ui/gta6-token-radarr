import sqlite3
import unittest
from research_volume_3000_shadow import decision, outcome, step


class VolumeShadowTests(unittest.TestCase):
    def test_boundary(self):
        self.assertEqual(decision(("t", "p", 2999, 50000))[0], "VOLUME_LT_3000")
        self.assertEqual(decision(("t", "p", 3000, 50000))[0], "VOLUME_GE_3000")
        self.assertEqual(decision(None)[0], "UNKNOWN_NO_PRE_SIGNAL_SNAPSHOT")

    def test_missing_quote_is_not_win(self):
        self.assertEqual(outcome(None)["return_pct"], None)

    def test_no_prebootstrap_leak(self):
        state={"version":1,"started_utc":"2026-11-12T13:00:00+00:00","candidates":{}}
        m1={"tracked":{"mint":{"registered_utc":"2026-11-12T12:00:00+00:00",
                               "signal":"2026-11-12T12:00:00+00:00"}}}
        db=sqlite3.connect(":memory:")
        self.assertEqual(step(state,m1,{"positions":{}},db),0)
        self.assertEqual(state["candidates"],{})


if __name__=="__main__":
    unittest.main()
