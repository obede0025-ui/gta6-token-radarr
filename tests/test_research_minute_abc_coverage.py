import sqlite3
import tempfile
import unittest
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import Mock

from research_minute_abc_coverage import cohort_metrics, parse_gecko, gecko_check, experiment

NOW=datetime(2026,11,16,15,0,tzinfo=timezone.utc)

def market(mint,pair,liq=None,vol=700):
    p={"chainId":"solana","baseToken":{"address":mint},"pairAddress":pair,
       "priceUsd":"0.1","volume":{"m5":vol},
       "txns":{"m5":{"buys":8,"sells":2}}}
    if liq is not None:p["liquidity"]={"usd":liq}
    return p

class ABCTests(unittest.TestCase):
    def test_cohort_missing_is_not_zero_liquidity(self):
        selected=[("m1","p1","time"),("m2","p2","time")]
        r=cohort_metrics(selected,[market("m1","p1"),market("m2","p2",35000)])
        self.assertEqual((r["known_liquidity"],r["unknown_liquidity"],
                          r["base_pass_without_age_or_momentum"]),(1,1,1))

    def test_gecko_exact_identity_only_and_missing(self):
        rows=[("m1","pool1","time"),("m2","pool2","time")]
        payload={"data":[
            {"id":"solana_pool1","attributes":{"address":"pool1","reserve_in_usd":"34000"}},
            {"id":"eth_pool2","attributes":{"address":"pool2","reserve_in_usd":"99000"}},
            {"id":"solana_pool3","attributes":{"address":"pool3","reserve_in_usd":"123"}}]}
        self.assertEqual(parse_gecko(payload,{"pool1","pool2"}),{"pool1":34000.0})
        response=Mock();response.json.return_value=payload
        session=Mock();session.get.return_value=response
        r=gecko_check(session,rows)
        self.assertEqual((r["available"],r["missing"]),(1,1))

    def test_gecko_limits_calls_to_ten_pools(self):
        rows=[("m"+str(i),"p"+str(i),"t") for i in range(15)]
        response=Mock();response.json.return_value={"data":[]}
        session=Mock();session.get.return_value=response
        r=gecko_check(session,rows)
        self.assertEqual(r["checked_pools"],10)
        session.get.assert_called_once()

    def test_invalid_limit_fails_closed(self):
        with self.assertRaises(ValueError):
            experiment("/missing","/missing",now=NOW,c_limit=30)

    def test_no_database_mutation_and_current_selection_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            original=Path(tmp)/"original.db"
            pilot=Path(tmp)/"pilot.db"
            with sqlite3.connect(original) as db:
                db.execute("""CREATE TABLE snapshots (
                  token_address TEXT,pair_address TEXT,scanned_at TEXT,
                  volume_m5 REAL,liquidity_usd REAL,pair_age_hours REAL)""")
                db.execute("INSERT INTO snapshots VALUES (?,?,?,?,?,?)",
                           ("mint","pair","2026-11-16T14:59:00+00:00",700,30000,1))
            with sqlite3.connect(pilot) as db:
                db.execute("""CREATE TABLE minute_observations (
                    observed_at TEXT,mint TEXT,pair TEXT,source_snapshot_at TEXT,
                    status TEXT,price_usd REAL,liquidity_usd REAL,
                    volume_m5 REAL,buys_m5 REAL,sells_m5 REAL,pair_age_hours REAL)""")
            origbytes=original.read_bytes();pilotbytes=pilot.read_bytes()
            d=Mock();d.json.return_value=[market("mint","pair")]
            g=Mock();g.json.return_value={"data":[{"id":"solana_pair",
                    "attributes":{"address":"pair","reserve_in_usd":"21000"}}]}
            session=Mock();session.get.side_effect=[d,g]
            # Existing candidate function always attempts optional shadow DB
            # and safely falls back if that DB is absent.
            r=experiment(original,pilot,now=NOW,session=session)
            self.assertEqual(r["A_current_selection"]["unknown_liquidity"],1)
            self.assertEqual(r["B_shadow_v2_selection"]["unknown_liquidity"],1)
            self.assertEqual(r["C_independent_pool_indexer"]["available"],1)
            self.assertEqual(original.read_bytes(),origbytes)
            self.assertEqual(pilot.read_bytes(),pilotbytes)

if __name__=="__main__":
    unittest.main()
