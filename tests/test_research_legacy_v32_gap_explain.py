import unittest
from research_legacy_v32_gap_explain import explain

class LegacyV32GapTests(unittest.TestCase):
    def test_identifies_bootstrap_skips_and_unexplained(self):
        m1={"tracked":{
            "old":{"registered_utc":"2026-11-12T10:00:00+00:00"},
            "known":{"registered_utc":"2026-11-13T12:00:00+00:00"},
            "new":{"registered_utc":"2026-11-13T12:30:00+00:00"},
            "active":{"registered_utc":"2026-11-13T13:00:00+00:00"},
        }}
        v32={"started_utc":"2026-11-13T11:00:00+00:00",
             "positions":{"active":{}},
             "skipped":{"known":"STALE"}}
        x=explain(m1,v32)
        self.assertEqual(x["missing_total"],3)
        self.assertEqual(x["classified"]["PRE_V32_BOOTSTRAP"],1)
        self.assertEqual(x["classified"]["RECORDED_SKIP"],1)
        self.assertEqual(x["classified"]["UNEXPLAINED_POST_BOOTSTRAP"],1)
        self.assertEqual(x["status"],"WARN")

    def test_bootstrap_only_is_not_misreported_as_broken_handoff(self):
        x=explain({"tracked":{"old":{"registered_utc":"2026-11-12T00:00:00+00:00"}}},
                  {"started_utc":"2026-11-13T00:00:00+00:00","positions":{}})
        self.assertEqual(x["status"],"PASS")
        self.assertEqual(x["classified"]["PRE_V32_BOOTSTRAP"],1)

    def test_real_legacy_created_utc_and_nested_signal(self):
        x=explain({"tracked":{
            "old":{"signal":{"scanned_at":"2026-11-09T16:00:00+00:00"}},
            "new":{"signal":{"scanned_at":"2026-11-09T18:00:00+00:00"}},
        }},{"created_utc":"2026-11-09T17:23:28+00:00",
             "positions":{}})
        self.assertEqual(x["v32_bootstrap_utc"],"2026-11-09T17:23:28+00:00")
        self.assertEqual(x["classified"]["PRE_V32_BOOTSTRAP"],1)
        self.assertEqual(x["classified"]["UNEXPLAINED_POST_BOOTSTRAP"],1)

    def test_missing_timestamp_needs_investigation(self):
        x=explain({"tracked":{"unknown":{}}},{"positions":{}})
        self.assertEqual(x["status"],"WARN")
        self.assertEqual(x["classified"]["TIMESTAMP_OR_BOOTSTRAP_UNKNOWN"],1)

if __name__=="__main__":
    unittest.main()
