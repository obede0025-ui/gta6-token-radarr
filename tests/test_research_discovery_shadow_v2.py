import sqlite3
import tempfile
import unittest
from datetime import datetime,timezone,timedelta
from pathlib import Path

from research_discovery_shadow_v2 import (
    discover,choose_pairs,make_sample,initialize,momentum,run,report,
    PROFILE_URL,BOOST_URL
)

BASE=datetime(2026,11,16,15,0,tzinfo=timezone.utc)
MINT_A="A"*43
MINT_B="B"*43

def market(mint,pool,vol=300,liq=30000,age=1,buys=8,sells=2):
    item={"chainId":"solana","pairAddress":pool,
          "baseToken":{"address":mint},"priceUsd":"1",
          "volume":{"m5":vol},"txns":{"m5":{"buys":buys,"sells":sells}},
          "pairCreatedAt":int((BASE-timedelta(hours=age)).timestamp()*1000)}
    if liq is not None:item["liquidity"]={"usd":liq}
    return item

class FakeResponse:
    def __init__(self,data):self.data=data
    def raise_for_status(self):return
    def json(self):return self.data

class Session:
    def __init__(self,profile,boost,markets):
        self.profile,self.boost,self.markets=profile,boost,markets
        self.calls=[]
    def get(self,url,timeout):
        self.calls.append(url)
        if url==PROFILE_URL:return FakeResponse(self.profile)
        if url==BOOST_URL:return FakeResponse(self.boost)
        mints=set(url.split("/")[-1].split(","))
        return FakeResponse([m for m in self.markets
              if m["baseToken"]["address"] in mints])

class V2Tests(unittest.TestCase):
    def source(self,path):
        with sqlite3.connect(path) as db:
            db.execute("""CREATE TABLE snapshots (
              token_address TEXT,pair_address TEXT,scanned_at TEXT,
              volume_m5 REAL,liquidity_usd REAL,pair_age_hours REAL)""")
            db.execute("INSERT INTO snapshots VALUES(?,?,?,?,?,?)",(
                MINT_A,"base_pool",BASE.isoformat(timespec="seconds"),800,35000,1))

    def test_discovery_dedup_sol_only(self):
        profile=[{"chainId":"solana","tokenAddress":MINT_B},
                 {"chainId":"ethereum","tokenAddress":MINT_A}]
        boosted=[{"chainId":"solana","tokenAddress":MINT_B}]
        self.assertEqual(discover(profile,boosted),{MINT_B:"BOOST+PROFILE"})

    def test_best_liquid_pair_without_faking_liquidity(self):
        chosen=choose_pairs([market(MINT_B,"missing",liq=None),
                             market(MINT_B,"known",liq=21000)],{MINT_B})
        self.assertEqual(chosen[MINT_B]["pairAddress"],"known")

    def test_spaced_sample_required(self):
        self.assertFalse(momentum(BASE,None,400,10,2)[0])
        prior=(BASE.isoformat(timespec="seconds"),300)
        self.assertTrue(momentum(BASE+timedelta(seconds=60),prior,400,10,2)[0])
        self.assertFalse(momentum(BASE+timedelta(seconds=60),prior,300,10,2)[0])

    def test_isolated_run_new_discovery_and_dedup(self):
        with tempfile.TemporaryDirectory() as folder:
            original=Path(folder)/"source.db";shadow=Path(folder)/"shadow.db"
            self.source(original)
            original_bytes=original.read_bytes()
            profile=[{"chainId":"solana","tokenAddress":MINT_B}]
            first=run(original,shadow,now=BASE,session=Session(
                profile,[],[market(MINT_A,"base_pool"),market(MINT_B,"new_pool")]))
            self.assertEqual(first["discovery_mints_not_in_baseline"],1)
            self.assertEqual(first["independent_feed"]["base_pass_including_pair_age"],1)
            self.assertEqual(first["new_unique_discovery_signals"],0)
            second=run(original,shadow,now=BASE+timedelta(seconds=60),
                session=Session(profile,[],[
                    market(MINT_A,"base_pool",vol=400),
                    market(MINT_B,"new_pool",vol=400)]))
            self.assertEqual(second["new_unique_discovery_signals"],1)
            duplicate=run(original,shadow,now=BASE+timedelta(seconds=60),
                session=Session(profile,[],[market(MINT_B,"new_pool",vol=400)]))
            self.assertEqual(duplicate["new_unique_discovery_signals"],0)
            with sqlite3.connect(shadow) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM v2_cycles").fetchone()[0],2)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM v2_signals WHERE cohort='DISCOVERY'").fetchone()[0],1)
            self.assertEqual(original.read_bytes(),original_bytes)
            self.assertEqual(report(shadow)["cycles"],2)

    def test_missing_liquidity_and_old_pair_fail_closed(self):
        with sqlite3.connect(":memory:") as db:
            initialize(db)
            unknown=make_sample(db,BASE,MINT_B,"pool","DISCOVERY","PROFILE",
                                market(MINT_B,"pool",liq=None))
            old=make_sample(db,BASE,MINT_B,"old","DISCOVERY","PROFILE",
                            market(MINT_B,"old",age=30))
            self.assertEqual(unknown[12],0)
            self.assertEqual(old[12],0)

    def test_invalid_limit_and_empty_report(self):
        with self.assertRaises(ValueError):
            run("/missing","/missing",now=BASE,max_discovered=100)
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(report(Path(folder)/"missing")["status"],"NO_SAMPLES")

if __name__=="__main__":unittest.main()
