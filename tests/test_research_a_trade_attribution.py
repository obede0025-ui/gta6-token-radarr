import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from research_a_trade_attribution import analyze

MINT = "9rbKkz2BDQBVgZmrHY6d7PTq2ge91gkpEyE4y9PPpump"

class TestAAttribution(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        p=Path(tmp.name)
        self.db=p/"market.db"
        self.state=p/"portfolio.json"
        self.state.write_text(json.dumps({"closed":{"A":[
            {"token":MINT,"pair":"PAIR1","name":"A","opened_at":"2026-11-20T12:00:00+00:00",
             "closed_at":"2026-11-20T12:10:00+00:00","reason":"TAKE_PROFIT","pnl_usd":8.0},
            {"token":MINT,"pair":"PAIR2","name":"B","opened_at":"2026-11-20T13:00:00+00:00",
             "closed_at":"2026-11-20T13:05:00+00:00","reason":"STOP_LOSS","pnl_usd":-5.0},
        ]},"open":{"A":{MINT:{"opened_at":"2026-11-20T14:00:00+00:00"}}}}))
        with sqlite3.connect(self.db) as c:
            c.execute("""CREATE TABLE snapshots(token_address TEXT,pair_address TEXT,
                scanned_at TEXT,price_usd REAL,liquidity_usd REAL,volume_m5 REAL,
                buys_m5 REAL,sells_m5 REAL,pair_age_hours REAL)""")
            c.executemany("INSERT INTO snapshots VALUES(?,?,?,?,?,?,?,?,?)", [
                (MINT,"PAIR1","2026-11-20T11:59:30+00:00",1.0,30000,1000,8,2,1),
                (MINT,"PAIR1","2026-11-20T12:00:30+00:00",2.0,1000000,5000,15,1,1),
                (MINT,"PAIR1","2026-11-20T12:10:00+00:00",1.4,20000,100,1,3,1),
                (MINT,"PAIR2","2026-11-20T12:40:00+00:00",2.0,10000,200,1,10,2)
            ])

    def test_chronology_censoring_and_no_writes(self):
        old_db=self.db.read_bytes()
        old_state=self.state.read_bytes()
        r=analyze(self.db,self.state)
        self.assertEqual(r["closed"]["count"],2)
        self.assertEqual(r["closed"]["pnl_usd"],3)
        self.assertEqual(r["total_loss_on_open_stress_pnl_usd"],-22)
        self.assertEqual(r["entry_evidence_coverage"]["fresh"],1)
        self.assertEqual(r["trades"][0]["entry_liquidity_usd"],30000)
        self.assertEqual(r["trades"][0]["entry_buy_share"],.8)
        self.assertEqual(r["trades"][0]["exit_liquidity_usd"],20000)
        self.assertIsNone(r["trades"][1]["entry_liquidity_usd"])
        self.assertEqual(r["trades"][0]["historical_sell_quote"],"UNKNOWN_NOT_IN_SOURCE")
        self.assertFalse(r["trades"][0]["executed_trade"])
        self.assertEqual(self.db.read_bytes(),old_db)
        self.assertEqual(self.state.read_bytes(),old_state)

    def test_reject_malformed_trade(self):
        state=json.loads(self.state.read_text())
        state["closed"]["A"][0]["opened_at"]="nonsense"
        self.state.write_text(json.dumps(state))
        with self.assertRaises(ValueError):
            analyze(self.db,self.state)

if __name__=="__main__":
    unittest.main()
