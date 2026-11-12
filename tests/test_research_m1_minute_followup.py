import sqlite3
import unittest
from datetime import datetime,timedelta,timezone
from research_m1_minute_followup import initialize,advance,follow

BASE=datetime(2026,11,12,15,0,tzinfo=timezone.utc)
def iso(n):return (BASE+timedelta(minutes=n)).isoformat(timespec="seconds")
def source(candidates):
    return {"mode":"MINUTE_B3_SAFETY_SHADOW","research_candidates":candidates}
def candidate(n=1):
    return {"mint":"M","pair":"P","signal_utc":iso(n),
            "observed_price_usd":1.0,"observed_liquidity_usd":30000,
            "observed_volume_m5":5000,"research_only":True}
def dbinit(db):
    db.execute("""CREATE TABLE minute_observations
      (observed_at TEXT,mint TEXT,pair TEXT,source_snapshot_at TEXT,status TEXT,
       price_usd REAL,liquidity_usd REAL,volume_m5 REAL,buys_m5 REAL,
       sells_m5 REAL,pair_age_hours REAL)""")
def add(db,n,price,liq=30000,status="QUOTE_MARKET_OBSERVED",pair="P"):
    db.execute("INSERT INTO minute_observations VALUES (?,?,?,?,?,?,?,?,?,?,?)",
              (iso(n),"M",pair,"source",status,price,liq,1000,10,2,1))

class TestM1(unittest.TestCase):
    def test_bootstrap_excludes_old_signals(self):
        with sqlite3.connect(":memory:") as db:
            dbinit(db)
            state=initialize(BASE+timedelta(minutes=5),source([candidate(4)]))
            self.assertEqual(advance(state,source([candidate(4)]),db,BASE+timedelta(minutes=6)),0)
            self.assertEqual(len(state["candidates"]),0)

    def test_one_signal_is_deduplicated_and_tp_is_spot_only(self):
        with sqlite3.connect(":memory:") as db:
            dbinit(db)
            state=initialize(BASE,source([]))
            c=candidate()
            self.assertEqual(advance(state,source([c]),db,BASE+timedelta(minutes=2)),1)
            add(db,2,1.02)
            add(db,3,1.12)
            self.assertEqual(advance(state,source([c]),db,BASE+timedelta(minutes=4)),0)
            row=list(state["candidates"].values())[0]
            self.assertEqual(row["followup"]["status"],"TP_SPOT_OBSERVED")
            self.assertEqual(row["followup"]["first_threshold_observation"]["utc"],iso(3))
            self.assertEqual(row["entry_observed_price_usd"],1.0)

    def test_unknown_liquidity_not_valid_exit(self):
        with sqlite3.connect(":memory:") as db:
            dbinit(db)
            state=initialize(BASE,source([]))
            c=candidate()
            advance(state,source([c]),db,BASE+timedelta(minutes=2))
            add(db,2,1.90,None,"LIQUIDITY_UNKNOWN")
            advance(state,source([c]),db,BASE+timedelta(minutes=3))
            f=list(state["candidates"].values())[0]["followup"]
            self.assertEqual(f["status"],"TRACKING")
            self.assertEqual(f["valid_followup_samples"],0)
            self.assertEqual(f["unknown_followup_samples"],1)

    def test_horizon_without_spot_is_not_a_filled_exit(self):
        with sqlite3.connect(":memory:") as db:
            dbinit(db)
            state=initialize(BASE,source([]))
            c=candidate()
            advance(state,source([c]),db,BASE+timedelta(minutes=2))
            advance(state,source([c]),db,BASE+timedelta(minutes=17))
            f=list(state["candidates"].values())[0]["followup"]
            self.assertEqual(f["status"],"HORIZON_NO_VALID_SPOT")
            self.assertIsNone(f["exit_spot_proxy_change_pct"])

    def test_exact_pair_no_leakage(self):
        with sqlite3.connect(":memory:") as db:
            dbinit(db)
            state=initialize(BASE,source([]))
            c=candidate()
            advance(state,source([c]),db,BASE+timedelta(minutes=2))
            add(db,3,1.50,pair="other")
            advance(state,source([c]),db,BASE+timedelta(minutes=4))
            self.assertEqual(list(state["candidates"].values())[0]["followup"]["valid_followup_samples"],0)

    def test_stale_registration_skipped(self):
        with sqlite3.connect(":memory:") as db:
            dbinit(db)
            state=initialize(BASE,source([]))
            self.assertEqual(advance(state,source([candidate(1)]),db,BASE+timedelta(minutes=6)),0)
            self.assertEqual(len(state["ignored"]),1)

if __name__=="__main__":unittest.main()
