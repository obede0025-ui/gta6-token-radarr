import sqlite3
import tempfile
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from research_a_minute_quote_join import analyze,quote_join

BASE=datetime(2026,11,20,12,0,tzinfo=timezone.utc)

class ResearchJoinTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.src=Path(self.tmp.name)/"source.db"
        self.minute=Path(self.tmp.name)/"minute.db"
        with sqlite3.connect(self.src) as c:
            c.execute("""CREATE TABLE snapshots(
               scanned_at TEXT,token_address TEXT,pair_address TEXT,
               price_usd REAL,liquidity_usd REAL,volume_m5 REAL,
               buys_m5 REAL,sells_m5 REAL,pair_age_hours REAL)""")
            for m,p,v in [(0,1,300),(5,1.02,400),(10,1.05,500)]:
                c.execute("INSERT INTO snapshots VALUES(?,?,?,?,?,?,?,?,?)",
                          ((BASE+timedelta(minutes=m)).isoformat(),"TOKEN","PAIR",p,30000,v,9,2,2))
        with sqlite3.connect(self.minute) as c:
            c.execute("""CREATE TABLE minute_observations(
               observed_at TEXT,mint TEXT,pair TEXT,status TEXT,
               price_usd REAL,liquidity_usd REAL,volume_m5 REAL,
               buys_m5 REAL,sells_m5 REAL)""")
            for seconds,p,liq,vol,b,s in [
                (10*60+40,1.05,30000,500,9,2),
                (11*60+40,1.08,31000,600,10,2),
                (13*60+40,1.15,32000,750,12,2),
                (15*60+40,1000,300,900,13,2)
            ]:
                c.execute("INSERT INTO minute_observations VALUES(?,?,?,?,?,?,?,?,?)",
                    ((BASE+timedelta(seconds=seconds)).isoformat(),"TOKEN","PAIR",
                     "QUOTE_MARKET_OBSERVED",p,liq,vol,b,s))
            c.execute("INSERT INTO minute_observations VALUES(?,?,?,?,?,?,?,?,?)",
                ((BASE+timedelta(minutes=15,seconds=40)).isoformat(),"TOKEN","WRONG",
                "QUOTE_MARKET_OBSERVED",1.2,50000,1000,20,1))

    def test_true_a_uses_prior_history_and_catches_liquidity_crash(self):
        before=self.src.read_bytes(),self.minute.read_bytes()
        r=analyze(self.src,self.minute,hours=1,as_of=BASE+timedelta(minutes=20))
        self.assertEqual(r["a_proxy_signals_after_15m_pair_cooldown"],1)
        item=r["records"][0]
        self.assertEqual(item["baseline"]["delay_from_signal_seconds"],40)
        self.assertEqual(item["horizons"]["60"]["status"],"OBSERVED")
        self.assertEqual(item["horizons"]["180"]["status"],"OBSERVED")
        self.assertEqual(item["horizons"]["300"]["liquidity_label"],"MOMENTUM_LIQUIDITY_COLLAPSE")
        self.assertEqual(item["quote_evidence"]["status"],"NO_MATCHED_PROSPECTIVE_COHORT")
        self.assertEqual(before,(self.src.read_bytes(),self.minute.read_bytes()))

    def test_quote_match_requires_strategy_time_and_exact_pair(self):
        signal=BASE+timedelta(minutes=10)
        co={"signals":{"TOKEN|PAIR":{"strategies":["A"],
             "signal_utc":signal.isoformat(),"first_quote_utc":(signal+timedelta(seconds=20)).isoformat(),
             "entry_quote_status":"BUY_AND_SELL_QUOTED",
             "followups":{"300":{"status":"SELL_QUOTE_ONLY","observed_utc":
                (signal+timedelta(seconds=325)).isoformat(),"sell_usdc_raw":"12500000"}}}}}
        q=quote_join(co,"TOKEN","PAIR",signal)
        self.assertEqual(q["followups_from_first_quote"]["300"]["status"],
                         "INDICATIVE_SELL_QUOTE_VERIFIED")
        self.assertEqual(quote_join(co,"TOKEN","WRONG",signal)["status"],
                         "NO_MATCHED_PROSPECTIVE_COHORT")
        self.assertEqual(quote_join(co,"TOKEN","PAIR",signal+timedelta(minutes=3))["status"],
                         "SIGNAL_TIME_MISMATCH")

if __name__=="__main__":unittest.main()
