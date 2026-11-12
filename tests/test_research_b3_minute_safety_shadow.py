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

    def test_missing_liquidity_always_blocked(self):
        row=("2026-11-12T15:00:00+00:00","mint","pair","t",
             "LIQUIDITY_UNKNOWN",1.0,None,5000,50,1,1)
        self.assertEqual(assess(row)["decision"],"LIQUIDITY_UNKNOWN")
        old=list(row)
        old[4]="MARKET_DATA_UNAVAILABLE"
        self.assertEqual(assess(old)["decision"],"LIQUIDITY_UNKNOWN")

    def test_base_pass_never_full_approval(self):
        row=("2026-11-12T15:00:00+00:00","mint","pair","t","QUOTE_MARKET_OBSERVED",.001,30000,4500,20,10,1)
        self.assertEqual(assess(row)["decision"],"BASE_FILTER_PASS_MOMENTUM_UNVERIFIED")

    def test_forward_momentum_research_candidate_deduplicated(self):
        with sqlite3.connect(":memory:") as db:
            db.execute("CREATE TABLE minute_observations (observed_at TEXT,mint TEXT,pair TEXT,source_snapshot_at TEXT,status TEXT,price_usd REAL,liquidity_usd REAL,volume_m5 REAL,buys_m5 REAL,sells_m5 REAL,pair_age_hours REAL)")
            base=datetime(2026,11,12,15,0,tzinfo=timezone.utc)
            def add(n,price):
                stamp=(base+timedelta(minutes=n)).isoformat(timespec="seconds")
                db.execute("INSERT INTO minute_observations VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (stamp,"mint","pair","source","QUOTE_MARKET_OBSERVED",price,30000,3500,15,4,1))
            for n in range(10):
                add(n,1.0)
            state=bootstrap(db,base+timedelta(minutes=9))
            add(10,1.10)
            result=advance(state,db,base+timedelta(minutes=10))
            self.assertEqual(result["new_research_candidates"],1)
            self.assertEqual(state["events"][-1]["momentum_research"]["status"],"MOMENTUM_MATCH_RESEARCH_ONLY")
            self.assertTrue(state["research_candidates"][0]["research_only"])
            self.assertEqual(advance(state,db,base+timedelta(minutes=10))["new_research_candidates"],0)
            add(11,1.11)
            self.assertEqual(advance(state,db,base+timedelta(minutes=11))["new_research_candidates"],0)
            self.assertEqual(len(state["research_candidates"]),1)

    def test_unknown_liquidity_never_research_signal(self):
        with sqlite3.connect(":memory:") as db:
            db.execute("CREATE TABLE minute_observations (observed_at TEXT,mint TEXT,pair TEXT,source_snapshot_at TEXT,status TEXT,price_usd REAL,liquidity_usd REAL,volume_m5 REAL,buys_m5 REAL,sells_m5 REAL,pair_age_hours REAL)")
            base=datetime(2026,11,12,15,0,tzinfo=timezone.utc)
            old=base.isoformat(timespec="seconds")
            db.execute("INSERT INTO minute_observations VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       (old,"mint","pair","source","LIQUIDITY_UNKNOWN",1.10,None,3500,15,4,1))
            state=bootstrap(db,base)
            later=(base+timedelta(minutes=1)).isoformat(timespec="seconds")
            db.execute("INSERT INTO minute_observations VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       (later,"mint","pair","source","LIQUIDITY_UNKNOWN",1.11,None,3500,15,4,1))
            result=advance(state,db,base+timedelta(minutes=1))
            self.assertEqual(result["new_research_candidates"],0)
            self.assertEqual(state["events"][-1]["momentum_research"]["status"],"BASE_FILTER_NOT_PASSED")

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
