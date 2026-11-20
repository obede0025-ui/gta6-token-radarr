"""Offline Token-2022 risk triage regression tests."""
import unittest
from datetime import datetime, timedelta, timezone
from radar_token_security import TOKEN_2022, evaluate_mint, LEGACY
from research_token2022_risk_diagnostics import assess_token2022_risk, WEIGHTS

MINT = "pcDL74F9AYQ8ConwemryWMHQX5LWbPHyAEbULGNpump"
EXT = [
    {"extension": "metadataPointer", "state": {"authority": None, "metadataAddress": MINT}},
    {"extension": "tokenMetadata", "state": {"mint": MINT, "updateAuthority": None, "additionalMetadata": []}},
]


def account(ext=None, owner=TOKEN_2022, mint_authority=None):
    return {"owner": owner, "executable": False,
            "data": {"parsed": {"type": "mint", "info": {
                "mintAuthority": mint_authority, "freezeAuthority": None,
                "decimals": 6, "supply": "1000000",
                "extensions": EXT if ext is None else ext}}}}


class RiskTests(unittest.TestCase):
    def test_metadata_clear_is_still_blocked_and_unknown_market(self):
        r = evaluate_mint(MINT, [], account())
        score = r["risk_diagnostics"]
        self.assertEqual(r["security_gate"], "BLOCK")
        self.assertEqual(r["live_trade_approval"], "BLOCK")
        self.assertEqual(score["status"], "RESEARCH_ONLY_BLOCKED")
        self.assertEqual(score["paper_trade_approval"], "BLOCK")
        self.assertEqual(score["live_trade_approval"], "BLOCK")
        self.assertEqual(score["checks"]["metadata"]["status"], "PASS")
        self.assertEqual(score["checks"]["other_extensions"]["status"], "PASS")
        self.assertEqual(score["checks"]["holder_concentration"]["status"], "UNKNOWN")
        self.assertEqual(score["checks"]["lp_control"]["status"], "UNKNOWN")
        self.assertEqual(score["risk_points_min"], 0)
        self.assertEqual(score["risk_points_max"], 45)
        self.assertEqual(sum(WEIGHTS.values()), 100)

    def test_active_mint_authority_is_failure_not_unknown(self):
        r = evaluate_mint(MINT, [], account(mint_authority=MINT))
        score = r["risk_diagnostics"]
        self.assertEqual(score["checks"]["mint_authority"]["status"], "FAIL")
        self.assertGreaterEqual(score["risk_points_min"], 10)
        self.assertEqual(r["security_gate"], "BLOCK")

    def test_risky_extension_is_failure(self):
        r = evaluate_mint(MINT, [], account(ext=EXT + [{"extension": "transferHook", "state": {}}]))
        self.assertEqual(r["risk_diagnostics"]["checks"]["other_extensions"]["status"], "FAIL")
        self.assertEqual(r["security_gate"], "BLOCK")

    def test_unknown_extension_is_unknown(self):
        r = evaluate_mint(MINT, [], account(ext=EXT + [{"extension": "futureExtension", "state": {}}]))
        self.assertEqual(r["risk_diagnostics"]["checks"]["other_extensions"]["status"], "UNKNOWN")
        self.assertEqual(r["security_gate"], "BLOCK")

    def test_unparseable_account_is_never_clear(self):
        r = evaluate_mint(MINT, [], None)
        self.assertEqual(r["risk_diagnostics"]["checks"]["program"]["status"], "UNKNOWN")
        self.assertEqual(r["risk_diagnostics"]["checks"]["mint_authority"]["status"], "UNKNOWN")
        self.assertEqual(r["security_gate"], "BLOCK")

    def test_legacy_program_unchanged(self):
        r = evaluate_mint(MINT, [], account(owner=LEGACY))
        self.assertEqual(r["program"], "SPL_TOKEN")
        self.assertEqual(r["security_gate"], "BASIC_CHECKS_CLEAR")
        self.assertIsNone(r["risk_diagnostics"])
        self.assertEqual(r["live_trade_approval"], "BLOCK")

    def test_wrong_and_stale_market_evidence_cannot_pass(self):
        r = evaluate_mint(MINT, [], account())
        now = datetime.now(timezone.utc)
        r["checked_utc"] = now.isoformat(timespec="seconds")
        good = {"mint": MINT, "checked_utc": now.isoformat(timespec="seconds"),
                "liquidity_usd": "30000", "sell_quote_available": True}
        result = assess_token2022_risk(r, good)
        self.assertEqual(result["checks"]["liquidity"]["status"], "PASS")
        self.assertEqual(result["checks"]["sell_route"]["status"], "PASS")
        self.assertEqual(result["live_trade_approval"], "BLOCK")
        for change in ({"mint": "A"*44},
                       {"checked_utc": (now - timedelta(minutes=10)).isoformat()},
                       {"sell_quote_available": "true"}):
            market = dict(good, **change)
            check = assess_token2022_risk(r, market)["checks"]
            if "sell_quote_available" in change:
                self.assertEqual(check["sell_route"]["status"], "UNKNOWN")
            else:
                self.assertEqual(check["liquidity"]["status"], "UNKNOWN")
                self.assertEqual(check["sell_route"]["status"], "UNKNOWN")

    def test_low_liquidity_and_missing_sell_quote_flag_fail(self):
        r = evaluate_mint(MINT, [], account())
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        r["checked_utc"] = now
        market = {"mint": MINT, "checked_utc": now,
                  "liquidity_usd": "1000", "sell_quote_available": False}
        result = assess_token2022_risk(r, market)
        self.assertEqual(result["checks"]["liquidity"]["status"], "FAIL")
        self.assertEqual(result["checks"]["sell_route"]["status"], "FAIL")
        self.assertGreaterEqual(result["risk_points_min"], 30)
        self.assertEqual(result["paper_trade_approval"], "BLOCK")


if __name__ == "__main__":
    unittest.main()
