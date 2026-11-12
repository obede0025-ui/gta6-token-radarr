import sqlite3
import tempfile
import unittest
from pathlib import Path
from datetime import datetime,timezone,timedelta
from research_b3_minute_safety_shadow import assess, bootstrap, advance

class Tests(unittest.TestCase):
    def test_unknown_not_synthetic_zero(self):
        row=("2026-11-12T15:00:00+00:00","mint","pair","t","MARKET_DATA_UNAVAILABLE",None,None,None,None,None,None)
        self.assertEqual(assess(row)["decision"],"MARKET_DATA_UNAVAILABLE")

    def test_base_pass_never_full_approval(self):
        row=("2026-11-12T15:00:00+00:00","mint","pair","t","QUOTE_MARKET_OBSERVED",.001,30000,4500,20,10,1)
        self.assertEqual(assess(row)["decision"],"BASE_FILTER_PASS_MOMENTUM_UNVERIFIED")

    def test_forward_only_and_no_source_write(self):
        with sqlite3.connect(":memory:") as db:
            db.execute("CREATE TABLE minute_observations (observed_at TEXT,mint TEXT,pair TEXT,source_snapshot_at TEXT,status TEXT,price_usd REAL,liquidity_usd REAL,volume_m5 REAL,buys_m5 REAL,sells_m5 REAL,pair_age_hours REAL)")
            before=datetime(2026,11,12,15,0,tzinfo=timezone.utc)
            db.execute("INSERT INTO minute_observations VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (before.isoformat(),"mint","pair","s","QUOTE_MARKET_OBSERVED",.001,30000,4500,20,10,1))
            state=bootstrap(db,before)
            self.assertEqual(advance(state,db,before)["added"],0)
            after=before+timedelta(minutes=1)
            db.execute("INSERT INTO minute_observations VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (after.isoformat(),"mint","pair","s","QUOTE_MARKET_OBSERVED",.001,30000,4500,20,10,1))
            self.assertEqual(advance(state,db,after)["added"],1)
            self.assertEqual(advance(state,db,after)["added"],0)

if __name__=="__main__":unittest.main()
