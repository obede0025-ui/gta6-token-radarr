import sqlite3
import unittest
from research_paper_pipeline_v1 import RULES, gate, advance, observe


class PipelineTests(unittest.TestCase):
    def test_gate(self):
        row = ("2026-11-12T14:00:00+00:00", "pair", 3000, 20000, 5, 3, 2, 0.001)
        self.assertEqual(gate(row), ("PASS", []))
        self.assertEqual(gate(("t", "p", 250, 20000, 5, 3, 2, 1))[0], "REJECT")
        self.assertEqual(gate(None)[0], "UNKNOWN_NO_SIGNAL_SNAPSHOT")

    def test_no_fill_without_quote(self):
        self.assertIsNone(observe(None)["net_quote_return_pct"])

    def test_bootstrap_and_freeze(self):
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE snapshots (token_address TEXT, pair_address TEXT, scanned_at TEXT, volume_m5 REAL, liquidity_usd REAL, buys_m5 INT, sells_m5 INT, pair_age_hours REAL, price_usd REAL)")
        db.execute("INSERT INTO snapshots VALUES ('new','pair','2026-11-12T14:01:00+00:00',4000,25000,10,2,1,0.01)")
        db.commit()
        state = {"version":1, "mode":"PAPER_PIPELINE_V1_SHADOW",
                 "started_utc":"2026-11-12T14:00:00+00:00", "rules": RULES, "candidates": {}}
        m1 = {"version":1, "tracked":{
            "old":{"pair":"pair","registered_utc":"2026-11-12T13:59:00+00:00","signal":"2026-11-12T14:01:00+00:00"},
            "new":{"pair":"pair","registered_utc":"2026-11-12T14:02:00+00:00","signal":"2026-11-12T14:01:00+00:00","status":"WATCHING"}
        }}
        v32 = {"positions": {}}
        self.assertEqual(advance(state,m1,v32,db),1)
        self.assertEqual(state["candidates"]["new"]["decision"], "PASS")
        db.execute("UPDATE snapshots SET volume_m5=0")
        self.assertEqual(advance(state,m1,v32,db),0)
        self.assertEqual(state["candidates"]["new"]["decision"], "PASS")
        db.close()


if __name__ == "__main__":
    unittest.main()
