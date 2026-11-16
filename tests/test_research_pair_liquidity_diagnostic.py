import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

from research_pair_liquidity_diagnostic import audit, classify

NOW=datetime(2026,11,16,15,0,tzinfo=timezone.utc)

def pair(mint,addr,liquidity=None,price="1"):
    item={"chainId":"solana","baseToken":{"address":mint},
          "pairAddress":addr,"priceUsd":price}
    if liquidity is not None:
        item["liquidity"]={"usd":liquidity}
    return item

class LiquidityDiagnosticTests(unittest.TestCase):
    def test_exact_liquidity_missing_even_if_alternative_known(self):
        r=classify("mint","pin",[pair("mint","pin"),pair("mint","alternate",40000)])
        self.assertEqual(r["classification"],"EXACT_LIQUIDITY_NOT_REPORTED")
        self.assertEqual(r["alternatives_with_known_liquidity"],1)
        self.assertEqual(r["top_alternative_research_only"]["pair"],"alternate")

    def test_missing_exact_distinct_from_missing_token(self):
        self.assertEqual(classify("mint","pin",[pair("mint","alt",500)])["classification"],
                         "PINNED_PAIR_NOT_RETURNED")
        self.assertEqual(classify("mint","pin",[])["classification"],
                         "TOKEN_NOT_RETURNED")

    def test_known_exact_pair_no_fabricated_unknown(self):
        r=classify("mint","pin",[pair("mint","pin",20000)])
        self.assertEqual(r["classification"],"EXACT_LIQUIDITY_NOW_REPORTED")

    def test_read_only_sqlite_and_bounded_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            dbpath=Path(tmp)/"market.sqlite"
            with sqlite3.connect(dbpath) as db:
                db.execute("""CREATE TABLE minute_observations (
                  observed_at TEXT,mint TEXT,pair TEXT,source_snapshot_at TEXT,
                  status TEXT,price_usd REAL,liquidity_usd REAL,
                  volume_m5 REAL,buys_m5 REAL,sells_m5 REAL,pair_age_hours REAL)""")
                db.execute("INSERT INTO minute_observations VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                           ("2026-11-16T14:59:00+00:00","mint","pin",
                            "2026-11-16T14:55:00+00:00","LIQUIDITY_UNKNOWN",
                            1,None,100,4,2,1))
            original=dbpath.read_bytes()
            response=Mock()
            response.json.return_value=[pair("mint","pin"),pair("mint","alt",40000)]
            session=Mock()
            session.get.return_value=response
            result=audit(dbpath,now=NOW,session=session)
            self.assertEqual(result["inspected_pairs"],1)
            self.assertEqual(result["with_viable_alternative_pair"],1)
            self.assertEqual(result["classifications"]["EXACT_LIQUIDITY_NOT_REPORTED"],1)
            session.get.assert_called_once()
            self.assertEqual(original,dbpath.read_bytes())

    def test_invalid_limit_fails_before_network(self):
        with self.assertRaises(ValueError):
            audit("/does/not/exist",now=NOW,limit=100)

if __name__=="__main__":
    unittest.main()
