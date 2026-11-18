import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from research_dashboard_v34 import render,main
from research_observer_health_v34 import metrics
from datetime import datetime,timezone
NOW=datetime(2026,11,18,21,0,tzinfo=timezone.utc)

class DashboardTests(unittest.TestCase):
    def test_escapes_untrusted_token_fields(self):
        ledger={"version":1,"signals":{"x":{"mint":"<script>alert(1)</script>",
            "strategy":"B","first_seen_utc":NOW.isoformat(),
            "paper_trade_approval":"BLOCK","live_trade_approval":"BLOCK",
            "followups":{},"buy_raw_out":None}},
            "paper_orders_executed":0,"live_orders_executed":0}
        report={"checked_utc":NOW.isoformat()}
        output=render(ledger,metrics(ledger,report,NOW))
        self.assertIn("&lt;script&gt;",output)
        self.assertNotIn("<script>alert",output)
        self.assertIn("Quote-only research",output)

    def test_no_source_files_overwritten(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            ledger=root/"ledger.json"; report=root/"report.json"
            output=root/"dashboard.html"
            state={"version":1,"signals":{},"paper_orders_executed":0,
                   "live_orders_executed":0}
            ledger.write_text(json.dumps(state))
            report.write_text(json.dumps({"checked_utc":NOW.isoformat()}))
            original=(ledger.read_bytes(),report.read_bytes())
            with patch("sys.argv",["dashboard","--ledger",str(ledger),
                "--report",str(report),"--output",str(output)]):
                main()
            self.assertTrue(output.exists())
            self.assertEqual((ledger.read_bytes(),report.read_bytes()),original)

    def test_html_path_does_not_alias_json(self):
        with tempfile.TemporaryDirectory() as td:
            ledger=Path(td)/"ledger.json"
            with patch("sys.argv",["dashboard","--ledger",str(ledger),
                "--output",str(ledger)]):
                with self.assertRaises(ValueError):main()

if __name__=="__main__":unittest.main()
