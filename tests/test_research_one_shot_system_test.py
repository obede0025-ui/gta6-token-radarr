import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from pathlib import Path
from research_one_shot_system_test import check, freshness, readable, evaluate

class OneShotSystemAuditTests(unittest.TestCase):
    def test_check_and_freshness(self):
        checks=[]
        now=datetime(2026,11,13,18,0,tzinfo=timezone.utc)
        freshness(checks,"recent","2026-11-13T17:59:00+00:00",now)
        freshness(checks,"stale","2026-11-13T17:50:00+00:00",now)
        self.assertEqual(checks[0]["status"],"PASS")
        self.assertEqual(checks[1]["status"],"WARN")

    def test_readable_report(self):
        result={"checked_utc":"2026-11-13T18:00:00+00:00",
                "verdict":"NEEDS_ATTENTION","summary":{"WARN":1},
                "checks":[{"name":"quality:data","status":"WARN","detail":"missing"}],
                "metrics":{"selection_ab":{"cycles_successful":7,"cycles_failed":0,
                    "known_liquidity":{"diff":20},"volume_ge_250":{"diff":10},
                    "base_filter_without_age_or_momentum":{"diff":14}},
                    "quote_evaluation_summary":{"positions":13}}}
        output=readable(result)
        self.assertIn("NEEDS_ATTENTION",output)
        self.assertIn("Base-pass delta: 14",output)
        self.assertIn("positions",output)

    def test_missing_root_is_reported_not_silently_passed(self):
        with patch("research_one_shot_system_test.run_tests") as mocked_tests:
            with patch("research_one_shot_system_test.services") as mocked_services:
                r=evaluate(root=Path("/tmp/gta6-absent-test-root"),
                    now=datetime(2026,11,13,18,0,tzinfo=timezone.utc),
                    with_tests=False)
        self.assertEqual(r["verdict"],"FAIL")
        self.assertGreater(r["summary"].get("FAIL",0),0)
        self.assertTrue(r["policy"]["read_only"])

if __name__=="__main__":
    unittest.main()
