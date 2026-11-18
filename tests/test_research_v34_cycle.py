import json
import tempfile
import unittest
from pathlib import Path
from datetime import datetime,timezone
from research_v34_cycle import cycle

NOW=datetime(2026,11,18,21,tzinfo=timezone.utc)

class Tests(unittest.TestCase):
    def test_produces_health_and_dashboard_only_in_dedicated_folder(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            db=root/"input.db";db.write_bytes(b"original DB sentinel")
            portfolio=root/"original_portfolio.json";portfolio.write_text('{"version":1}')
            before=(db.read_bytes(),portfolio.read_bytes())
            def fake_run(db_path,state_path,ledger,report):
                ledger.parent.mkdir(parents=True,exist_ok=True)
                ledger.write_text(json.dumps({"version":1,"signals":{},
                    "paper_orders_executed":0,"live_orders_executed":0}))
                result={"checked_utc":NOW.isoformat(),"new_count":0}
                report.write_text(json.dumps(result))
                return result
            result=cycle(db,portfolio,root/"v33",clock=lambda:NOW,runner=fake_run)
            self.assertEqual(result["health_status"],"OK")
            self.assertEqual((db.read_bytes(),portfolio.read_bytes()),before)
            self.assertTrue((root/"v33"/"research_health.json").is_file())
            self.assertIn("No filled orders", (root/"v33"/"research_dashboard.html").read_text())

    def test_missing_sources_and_alias_are_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"missing"
            with self.assertRaises(ValueError):cycle(p,p,Path(d))
            with self.assertRaises(FileNotFoundError):cycle(p,Path(d)/"portfolio",Path(d))

if __name__=="__main__":unittest.main()
