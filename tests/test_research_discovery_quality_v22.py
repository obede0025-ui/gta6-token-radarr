import sqlite3
import tempfile
import unittest
from datetime import datetime,timezone,timedelta
from pathlib import Path
from research_discovery_quality_v22 import execute, report, rank_from_history, PROFILE_URL, BOOST_URL

START=datetime(2026,11,16,15,0,tzinfo=timezone.utc)
MINTS=["A"*42+str(i) for i in range(4)]

def market(mint,vol=300,liq=30000):
    return {"chainId":"solana","baseToken":{"address":mint},
       "pairAddress":"pool"+mint[-1],"priceUsd":"1",
       "liquidity":{"usd":liq},"volume":{"m5":vol},
       "txns":{"m5":{"buys":10,"sells":2}},
       "pairCreatedAt":int((START-timedelta(hours=1)).timestamp()*1000)}

class FakeResponse:
    def __init__(self,data):self.data=data
    def raise_for_status(self):return None
    def json(self):return self.data

class Session:
    def __init__(self,items):self.items=items;self.calls=[]
    def get(self,url,timeout):
        self.calls.append(url)
        if url==PROFILE_URL:
            return FakeResponse([{"chainId":"solana","tokenAddress":m} for m in MINTS])
        if url==BOOST_URL:return FakeResponse([])
        wanted=set(url.split("/")[-1].split(","))
        return FakeResponse([p for p in self.items if p["baseToken"]["address"] in wanted])

class QualityTests(unittest.TestCase):
    def test_no_current_snapshot_lookahead(self):
        previous={MINTS[3]:("2026-11-16T14:59:00+00:00","pool3",
                             35000,1500,10,2,1,1)}
        self.assertEqual(rank_from_history(MINTS,{},2),MINTS[:2])
        self.assertEqual(rank_from_history(MINTS,previous,2),[MINTS[3],MINTS[0]])

    def test_two_cohorts_and_dedup_no_existing_state_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            shadow=Path(tmp)/"v22.sqlite"
            first=execute(shadow,START,Session([
                market(MINTS[0],vol=100),market(MINTS[1],vol=100),
                market(MINTS[2],vol=300),market(MINTS[3],vol=600)]),slots=2)
            self.assertEqual(first["selection_changed"],0)
            self.assertEqual(first["new_unique_signals"]["feed_order"],0)
            # At next timestamp, activity-first must use previous stored
            # measurements: 3 and 2 rank ahead of 0 and 1.
            second=execute(shadow,START+timedelta(seconds=60),Session([
                market(MINTS[0],vol=120),market(MINTS[1],vol=120),
                market(MINTS[2],vol=400),market(MINTS[3],vol=800)]),slots=2)
            self.assertEqual(second["selection_changed"],2)
            self.assertEqual(second["activity_first"]["base_pass_including_pair_age"],2)
            self.assertEqual(second["feed_order"]["base_pass_including_pair_age"],0)
            # V2.2 cohort momentum requires its OWN previous selected
            # observation, not the unselected universe measurements.
            self.assertEqual(second["activity_first"]["spaced_momentum_pass"],0)
            third=execute(shadow,START+timedelta(seconds=120),Session([
                market(MINTS[0],vol=120),market(MINTS[1],vol=120),
                market(MINTS[2],vol=480),market(MINTS[3],vol=960)]),slots=2)
            self.assertEqual(third["activity_first"]["spaced_momentum_pass"],2)
            self.assertEqual(third["new_unique_signals"]["activity_first"],2)
            duplicate=execute(shadow,START+timedelta(seconds=120),Session([
                market(MINTS[2],vol=480),market(MINTS[3],vol=960)]),slots=2)
            self.assertEqual(duplicate["status"],"DUPLICATE_SECOND_SKIPPED")
            r=report(shadow)
            self.assertEqual(r["cycles"],3)
            self.assertEqual(r["unique_signal_pairs"]["ACTIVITY_FIRST"],2)
            with sqlite3.connect(shadow) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM quality_cycles").fetchone()[0],3)

    def test_unknown_liquidity_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            shadow=Path(tmp)/"v22.sqlite"
            items=[market(m,liq=1000) for m in MINTS]
            result=execute(shadow,START,Session(items),slots=2)
            self.assertEqual(result["activity_first"]["base_pass_including_pair_age"],0)

    def test_limit_validation(self):
        with self.assertRaises(ValueError):
            execute("/no/such/location",START,Session([]),slots=100)

if __name__=="__main__":unittest.main()
