import sqlite3
import tempfile
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from research_discovery_quote_v27 import (
    initialize,collect_new_signals,followup,report,run,Quoter,USDC,BUY_USDC_RAW
)
START=datetime(2026,11,16,19,0,tzinfo=timezone.utc)
MINT="A"*44
PAIR="B"*44

class FakeQuoter:
    def __init__(self):self.calls=[]
    def quote(self,source,target,amount):
        self.calls.append((source,target,amount))
        return 100_000 if source==USDC else 11_000_000
    def close(self):pass

class FakeResponse:
    def raise_for_status(self):pass
    def json(self):return {"inputMint":USDC,"outputMint":MINT,"outAmount":"100000"}

class FakeSession:
    def __init__(self):self.calls=[]
    def get(self,url,params,timeout):
        self.calls.append((url,params))
        return FakeResponse()

class Tests(unittest.TestCase):
    def source(self,folder):
        path=folder/"source.sqlite"
        with sqlite3.connect(path) as db:
            db.execute("""CREATE TABLE quality_cohort_samples(
               observed_at TEXT,mint TEXT,pair TEXT,cohort TEXT,
               price_usd REAL,liquidity_usd REAL,momentum_pass INT)""")
            db.executemany("INSERT INTO quality_cohort_samples VALUES(?,?,?,?,?,?,?)",[
              (START.isoformat(timespec="seconds"),MINT,PAIR,cohort,0.001,25000,1)
              for cohort in ("FEED_ORDER","ACTIVITY_FIRST")])
        return path

    def test_first_signal_dedup_cohorts_and_followups(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)
            src=self.source(folder);dest=folder/"out.sqlite"
            q=FakeQuoter()
            with sqlite3.connect(dest) as db:
                initialize(db)
                self.assertEqual(collect_new_signals(src,db,START,q),1)
                self.assertEqual(collect_new_signals(src,db,START,q),0)
                self.assertEqual(len(q.calls),2)
                self.assertEqual(followup(db,START+timedelta(seconds=60),
                    q,now_fn=lambda:START+timedelta(seconds=60))[0],1)
                self.assertEqual(followup(db,START+timedelta(seconds=300),
                    q,now_fn=lambda:START+timedelta(seconds=300))[0],1)
                self.assertEqual(followup(db,START+timedelta(seconds=900),
                    q,now_fn=lambda:START+timedelta(seconds=900))[0],1)
            out=report(dest)
            self.assertEqual(out["unique_first_signal_pairs"],1)
            self.assertEqual(out["signals"][0]["cohorts"],"ACTIVITY_FIRST+FEED_ORDER")
            self.assertEqual(out["signals"][0]["outcomes"]["900"]["paper_quote_return_pct_before_network_fees"],10.)

    def test_late_horizon_is_missing_and_cannot_backfill(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);src=self.source(folder);dest=folder/"out.sqlite"
            q=FakeQuoter()
            with sqlite3.connect(dest) as db:
                initialize(db)
                collect_new_signals(src,db,START,q)
                followup(db,START+timedelta(seconds=1100),q,
                         now_fn=lambda:START+timedelta(seconds=1100))
            out=report(dest)
            self.assertEqual(out["signals"][0]["outcomes"]["900"]["status"],"HORIZON_MISSED")
            self.assertIsNone(out["signals"][0]["outcomes"]["900"]["paper_quote_return_pct_before_network_fees"])

    def test_quote_only_get_and_amount(self):
        s=FakeSession()
        q=Quoter(session=s,clock=lambda:1,sleep=lambda _:None)
        self.assertEqual(q.quote(USDC,MINT,BUY_USDC_RAW),100000)
        self.assertEqual(len(s.calls),1)
        with self.assertRaises(ValueError):q.quote(USDC,MINT,0)

    def test_never_overwrite_old_files(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);source=self.source(folder);out=folder/"out.sqlite"
            with self.assertRaises(FileExistsError):run(source,out)
        with self.assertRaises(ValueError):run(cycles=20)

if __name__=="__main__":unittest.main()
