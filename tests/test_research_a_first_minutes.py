import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from research_a_first_minutes import evaluate

NOW=datetime(2026,11,20,18,10,tzinfo=timezone.utc)
MINT="9rbKkz2BDQBVgZmrHY6d7PTq2ge91gkpEyE4y9PPpump"

class FirstMinutesTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        p=Path(temp.name)
        self.db,self.cohort=p/"db.sqlite",p/"focus.json"
        self.cohort.write_text(json.dumps({"version":1,"signals":{"a":{
            "mint":MINT,"pair":"P1","signal_utc":"2026-11-20T18:00:00+00:00",
            "first_quote_utc":"2026-11-20T18:00:05+00:00",
            "strategies":["A"],"initial_buy_raw":"1000","initial_sell_usdc_raw":"9500000",
            "entry_quote_status":"BUY_AND_SELL_QUOTED",
            "followups":{"60":{"status":"SELL_QUOTE_ONLY","sell_usdc_raw":"11000000"}}}}}))
        with sqlite3.connect(self.db) as conn:
            conn.execute("""CREATE TABLE snapshots(scanned_at TEXT,token_address TEXT,
            pair_address TEXT, price_usd REAL, liquidity_usd REAL, volume_m5 REAL,
            buys_m5 REAL, sells_m5 REAL)""")
            for sec,price,liq,vol,buy,sell in [
                (-5,1,25000,1000,7,3),(0,1,26000,1200,8,2),
                (65,1.1,28000,1800,9,1),(185,1.2,32000,2000,8,2),
                (430,1.8,50000,3000,9,1)]:
                t=datetime(2026,11,20,18,0,tzinfo=timezone.utc)+timedelta(seconds=sec)
                conn.execute("INSERT INTO snapshots VALUES(?,?,?,?,?,?,?,?)",
                  (t.isoformat(),MINT,"P1",price,liq,vol,buy,sell))

    def test_future_baseline_not_leaked_and_missing_five_min(self):
        old_db=self.db.read_bytes()
        old_state=self.cohort.read_bytes()
        result=evaluate(self.db,self.cohort,now=NOW)
        entry=result["records"][0]
        self.assertEqual(entry["baseline"]["price_usd"],1)
        self.assertEqual(entry["baseline"]["liquidity_usd"],26000)
        self.assertEqual(entry["horizons"]["60"]["market_status"],"OBSERVED")
        self.assertEqual(entry["horizons"]["60"]["price_change_pct"],10)
        self.assertEqual(entry["horizons"]["180"]["market_status"],"OBSERVED")
        self.assertEqual(entry["horizons"]["300"]["market_status"],"MISSING_OR_LATE")
        self.assertEqual(entry["quote_followups_from_first_quote"]["60"]["quote_return_pct_before_fees"],10)
        self.assertEqual(self.db.read_bytes(),old_db)
        self.assertEqual(self.cohort.read_bytes(),old_state)

    def test_not_due_does_not_claim_market_result(self):
        t=datetime(2026,11,20,18,1,30,tzinfo=timezone.utc)
        r=evaluate(self.db,self.cohort,now=t)
        self.assertEqual(r["records"][0]["horizons"]["300"]["market_status"],"NOT_DUE")

    def test_wrong_pair_not_substituted(self):
        with sqlite3.connect(self.db) as c:
            c.execute("DELETE FROM snapshots WHERE pair_address='P1'")
            c.execute("INSERT INTO snapshots VALUES(?,?,?,?,?,?,?,?)",
              ("2026-11-20T18:00:00+00:00",MINT,"OTHER",1,25000,1000,7,3))
        r=evaluate(self.db,self.cohort,now=NOW)
        self.assertIsNone(r["records"][0]["baseline"])

    def test_invalid_cohort_refused(self):
        self.cohort.write_text('{"version":1,"signals":[]}')
        with self.assertRaises(ValueError):
            evaluate(self.db,self.cohort,now=NOW)

if __name__=="__main__":unittest.main()
