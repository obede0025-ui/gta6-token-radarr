"""V3.5 offline tests. No external HTTP, trading, DB mutation or timers."""
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from radar_token_security import TOKEN_2022
from research_v35_market_evidence import cycle, select_fresh

MINT = "pcDL74F9AYQ8ConwemryWMHQX5LWbPHyAEbULGNpump"
MINT2 = "5tAB1DMgrsJKgaKcfCfj1RzumuqovQP1LuxzQ7thpump"
NOW = datetime.now(timezone.utc).replace(microsecond=0)


def account():
    return {"owner": TOKEN_2022, "executable": False,
            "data": {"parsed": {"type": "mint", "info": {
                "mintAuthority": None, "freezeAuthority": None,
                "decimals": 6, "supply": "1000000", "extensions": [
                    {"extension": "metadataPointer", "state": {
                        "authority": None, "metadataAddress": MINT}},
                    {"extension": "tokenMetadata", "state": {
                        "mint": MINT, "updateAuthority": None,
                        "additionalMetadata": []}},
                ]}}}}


class Quote:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []
    def quote(self, mint, other, amount):
        self.calls.append((mint, other, amount))
        if self.fail:
            raise ValueError("unavailable")
        return 9500000


class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.db = root / "learning.db"
        self.ledger = root / "research_ledger.json"
        self.output = root / "v35" / "report.json"
        with sqlite3.connect(self.db) as conn:
            conn.execute("CREATE TABLE snapshots (token_address TEXT, scanned_at TEXT, liquidity_usd REAL)")
            conn.executemany("INSERT INTO snapshots VALUES (?,?,?)", [
                (MINT, (NOW - timedelta(seconds=20)).isoformat(), 45000),
                (MINT2, (NOW - timedelta(minutes=20)).isoformat(), 200000),
                (MINT, (NOW - timedelta(minutes=15)).isoformat(), 200),
            ])
        ledger = {"signals": {
            "key1": {"mint": MINT, "first_seen_utc": (NOW-timedelta(days=1)).isoformat(),
                     "buy_raw_out": "8000000"},
            "key2": {"mint": MINT2, "first_seen_utc": (NOW-timedelta(days=1)).isoformat(),
                     "buy_raw_out": "7000000"},
        }, "paper_orders_executed": 0, "live_orders_executed": 0}
        self.ledger.write_text(json.dumps(ledger))

    def test_fresh_selection_deduplicates_and_rejects_stale(self):
        rows, stats = select_fresh(self.db, self.ledger, NOW)
        self.assertEqual([r["mint"] for r in rows], [MINT])
        self.assertEqual(stats["ledger_mints"], 2)
        self.assertEqual(stats["fresh_mints"], 1)
        self.assertEqual(stats["stale_or_missing_mints"], 1)

    def test_cycle_quote_only_with_matching_fresh_mint(self):
        quoter = Quote()
        with patch("research_v35_market_evidence.rpc_get_account", return_value=account()):
            r = cycle(self.db, self.ledger, self.output, now=NOW,
                      clock=lambda: NOW, rpc_session=object(), quoter=quoter)
        self.assertEqual(len(r["results"]), 1)
        result = r["results"][0]
        self.assertEqual(result["quote_status"], "QUOTE_ONLY_OK")
        self.assertEqual(result["sell_quote_usdc_raw"], "9500000")
        self.assertEqual(result["risk"]["checks"]["liquidity"]["status"], "PASS")
        self.assertEqual(result["risk"]["checks"]["sell_route"]["status"], "PASS")
        self.assertEqual(result["risk"]["checks"]["holder_concentration"]["status"], "UNKNOWN")
        self.assertEqual(result["security_gate"], "BLOCK")
        self.assertEqual(result["live_trade_approval"], "BLOCK")
        self.assertEqual(r["source_db_writes"], 0)
        self.assertEqual(r["source_ledger_writes"], 0)
        self.assertEqual(len(quoter.calls), 1)
        self.assertEqual(json.loads(self.output.read_text())["results"][0]["mint"], MINT)

    def test_unavailable_jupiter_is_unknown_not_fail_or_pass(self):
        with patch("research_v35_market_evidence.rpc_get_account", return_value=account()):
            r = cycle(self.db, self.ledger, now=NOW, clock=lambda: NOW,
                      rpc_session=object(), quoter=Quote(fail=True))
        check = r["results"][0]["risk"]["checks"]["sell_route"]
        self.assertEqual(check["status"], "UNKNOWN")
        self.assertEqual(r["results"][0]["live_trade_approval"], "BLOCK")

    def test_rpc_error_yields_unknown_security(self):
        with patch("research_v35_market_evidence.rpc_get_account", side_effect=ValueError("RPC_ERROR")):
            r = cycle(self.db, self.ledger, now=NOW, clock=lambda: NOW,
                      rpc_session=object(), quoter=Quote())
        self.assertEqual(r["results"][0]["rpc_status"], "UNKNOWN")
        self.assertEqual(r["results"][0]["risk"]["checks"]["program"]["status"], "UNKNOWN")
        self.assertEqual(r["results"][0]["security_gate"], "BLOCK")

    def test_stale_after_probe_invalidates_market(self):
        advanced = NOW + timedelta(minutes=3)
        def clock():
            return advanced
        with patch("research_v35_market_evidence.rpc_get_account", return_value=account()):
            r = cycle(self.db, self.ledger, now=NOW,
                      clock=clock, rpc_session=object(), quoter=Quote())
        row = r["results"][0]
        self.assertEqual(row["quote_status"], "MARKET_SNAPSHOT_STALE_AFTER_PROBE")
        self.assertEqual(row["risk"]["checks"]["liquidity"]["status"], "UNKNOWN")
        self.assertEqual(row["risk"]["checks"]["sell_route"]["status"], "UNKNOWN")

    def test_old_snapshots_cannot_be_used_in_live_scoring(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE snapshots SET scanned_at=?", ((NOW-timedelta(hours=3)).isoformat(),))
        with patch("research_v35_market_evidence.rpc_get_account") as rpc:
            q = Quote()
            r = cycle(self.db, self.ledger, now=NOW, clock=lambda: NOW,
                      rpc_session=object(), quoter=q)
        self.assertEqual(r["results"], [])
        rpc.assert_not_called()
        self.assertEqual(q.calls, [])

    def test_no_source_overwrite_and_bounded_limit(self):
        with self.assertRaises(ValueError):
            cycle(self.db, self.ledger, self.db, now=NOW, clock=lambda: NOW)
        with self.assertRaises(ValueError):
            cycle(self.db, self.ledger, self.ledger, now=NOW, clock=lambda: NOW)
        with self.assertRaises(ValueError):
            select_fresh(self.db, self.ledger, NOW, 999)


if __name__ == "__main__":
    unittest.main()
