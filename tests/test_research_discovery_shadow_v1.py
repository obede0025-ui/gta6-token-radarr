import sqlite3
import tempfile
import unittest
from datetime import datetime,timezone,timedelta
from pathlib import Path
from unittest.mock import Mock

from research_discovery_shadow_v1 import run, report, momentum, initialize

BASE=datetime(2026,11,16,15,0,tzinfo=timezone.utc)

def token_payload(mint,pair,volume=300,liq=30000,buys=9,sells=2):
    item={"chainId":"solana","baseToken":{"address":mint},
          "pairAddress":pair,"priceUsd":"0.1",
          "volume":{"m5":volume},"txns":{"m5":{"buys":buys,"sells":sells}}}
    if liq is not None:item["liquidity"]={"usd":liq}
    return item

class Session:
    def __init__(self,payload):
        self.payload=payload
        self.calls=[]
    def get(self,url,timeout):
        self.calls.append(url)
        response=Mock()
        requested=set(url.split("/")[-1].split(","))
        response.json.return_value=[p for p in self.payload
                          if p["baseToken"]["address"] in requested]
        return response

class DiscoveryShadowTests(unittest.TestCase):
    def make_source(self,path,n=40):
        with sqlite3.connect(path) as db:
            db.execute("""CREATE TABLE snapshots (
              token_address TEXT,pair_address TEXT,scanned_at TEXT,
              volume_m5 REAL,liquidity_usd REAL,pair_age_hours REAL)""")
            db.executemany("INSERT INTO snapshots VALUES (?,?,?,?,?,?)",[
                (f"mint{i:03d}",f"pool{i:03d}",BASE.isoformat(timespec="seconds"),
                 3000-i,30000,1) for i in range(n)])

    def test_first_and_second_run_and_dedup(self):
        with tempfile.TemporaryDirectory() as folder:
            src=Path(folder)/"learning.db"
            out=Path(folder)/"shadow.db"
            self.make_source(src,40)
            before=src.read_bytes()
            payload=[token_payload(f"mint{i:03d}",f"pool{i:03d}",volume=300)
                     for i in range(40)]
            s1=Session(payload)
            first=run(src,out,BASE,s1,limit=40)
            self.assertEqual(first["baseline"]["selected"],30)
            self.assertEqual(first["expanded"]["selected"],40)
            self.assertEqual(first["baseline"]["base_pass_without_age_or_B3_momentum"],30)
            self.assertEqual(first["expanded"]["shadow_volume_momentum_pass"],0)
            self.assertEqual(len(s1.calls),2)
            grown=[token_payload(f"mint{i:03d}",f"pool{i:03d}",volume=400)
                   for i in range(40)]
            second=run(src,out,BASE+timedelta(seconds=60),Session(grown),limit=40)
            self.assertEqual(second["new_unique_shadow_signals"],40)
            self.assertEqual(second["expanded_minus_baseline"]["volume_momentum"],10)
            duplicate=run(src,out,BASE+timedelta(seconds=60),Session(grown),limit=40)
            self.assertEqual(duplicate["new_unique_shadow_signals"],0)
            with sqlite3.connect(out) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM discovery_samples").fetchone()[0],80)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM discovery_signals").fetchone()[0],40)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM discovery_cycles").fetchone()[0],2)
            self.assertEqual(src.read_bytes(),before)
            self.assertEqual(report(out)["unique_shadow_signal_pairs"],40)

    def test_no_liquidity_no_approval_and_gap(self):
        with tempfile.TemporaryDirectory() as folder:
            src=Path(folder)/"learning.db"
            out=Path(folder)/"shadow.db"
            self.make_source(src,30)
            payload=[token_payload(f"mint{i:03d}",f"pool{i:03d}",
                                   volume=300,liq=None if i<10 else 30000)
                     for i in range(30)]
            first=run(src,out,BASE,Session(payload),limit=30)
            self.assertEqual(first["expanded"]["unknown_liquidity"],10)
            self.assertEqual(first["expanded"]["base_pass_without_age_or_B3_momentum"],20)
            increased=[token_payload(f"mint{i:03d}",f"pool{i:03d}",
                                     volume=400,liq=None if i<10 else 30000)
                       for i in range(30)]
            second=run(src,out,BASE+timedelta(seconds=60),Session(increased),limit=30)
            self.assertEqual(second["new_unique_shadow_signals"],20)

    def test_momentum_requires_spacing_volume_and_buys(self):
        previous=(BASE.isoformat(timespec="seconds"),300,9,2)
        self.assertEqual(momentum(BASE+timedelta(seconds=30),previous,400,10,2)[1],"SAMPLES_TOO_CLOSE")
        self.assertEqual(momentum(BASE+timedelta(seconds=60),previous,300,10,2)[1],"M5_VOLUME_NOT_GROWING")
        self.assertEqual(momentum(BASE+timedelta(seconds=60),previous,400,2,10)[1],"BUY_PRESSURE_NOT_CONFIRMED")
        self.assertEqual(momentum(BASE+timedelta(seconds=700),previous,400,10,2)[1],"PREVIOUS_SAMPLE_TOO_OLD")
        self.assertTrue(momentum(BASE+timedelta(seconds=60),previous,400,10,2)[0])

    def test_duplicate_identity_is_pair_scoped_and_empty_report(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"empty.db"
            self.assertEqual(report(path)["status"],"NO_SAMPLES")
            with sqlite3.connect(path) as db:
                initialize(db)
                db.execute("INSERT INTO discovery_signals VALUES(?,?,?,?)",
                           ("mint","pool1",BASE.isoformat(),1))
                db.execute("INSERT INTO discovery_signals VALUES(?,?,?,?)",
                           ("mint","pool2",BASE.isoformat(),0))
                self.assertEqual(db.execute("SELECT COUNT(*) FROM discovery_signals").fetchone()[0],2)

    def test_limit_validation(self):
        with self.assertRaises(ValueError):
            run("/missing","/missing",BASE,Session([]),limit=120)

if __name__=="__main__":
    unittest.main()
