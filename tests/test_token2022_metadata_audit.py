"""Offline, fail-closed regression tests for Token-2022 metadata diagnostics."""
import copy
import unittest
from radar_token_security import TOKEN_2022, evaluate_mint
from research_token2022_metadata_audit import audit_metadata_extensions

MINT = "pcDL74F9AYQ8ConwemryWMHQX5LWbPHyAEbULGNpump"


def extensions():
    return [
        {"extension": "metadataPointer", "state": {"authority": None, "metadataAddress": MINT}},
        {"extension": "tokenMetadata", "state": {
            "mint": MINT, "updateAuthority": None, "additionalMetadata": [],
            "name": "Fleeca Coin", "symbol": "FLC", "uri": "https://example.invalid/metadata"}},
    ]


def account(ext):
    return {"owner": TOKEN_2022, "executable": False,
            "data": {"parsed": {"type": "mint", "info": {
                "mintAuthority": None, "freezeAuthority": None, "decimals": 6,
                "supply": "1000000", "extensions": ext}}}}


class MetadataAuditTests(unittest.TestCase):
    def test_report_is_diagnostic_not_trade_permission(self):
        row = audit_metadata_extensions(MINT, extensions())
        self.assertEqual(row["status"], "METADATA_READ_ONLY_CHECKS_CLEAR")
        self.assertEqual(row["trade_approval"], "BLOCK")
        sec = evaluate_mint(MINT, ["research"], account(extensions()))
        self.assertEqual(sec["program"], "TOKEN_2022")
        self.assertEqual(sec["metadata_audit"]["status"], "METADATA_READ_ONLY_CHECKS_CLEAR")
        self.assertEqual(sec["security_gate"], "BLOCK")
        self.assertEqual(sec["live_trade_approval"], "BLOCK")
        self.assertIn("TOKEN_2022_REQUIRES_EXTENSION_AUDIT", sec["reasons"])

    def test_metadata_pointer_mutable_is_rejected(self):
        ext = extensions()
        ext[0]["state"]["authority"] = MINT
        r = audit_metadata_extensions(MINT, ext)
        self.assertEqual(r["status"], "BLOCK_INCOMPLETE")
        self.assertIn("METADATA_POINTER_AUTHORITY_ACTIVE_OR_UNKNOWN", r["reasons"])

    def test_metadata_update_authority_mutable_is_rejected(self):
        ext = extensions()
        ext[1]["state"]["updateAuthority"] = MINT
        self.assertIn("TOKEN_METADATA_UPDATE_AUTHORITY_ACTIVE_OR_UNKNOWN",
                      audit_metadata_extensions(MINT, ext)["reasons"])

    def test_mismatching_mint_and_pointer_are_rejected(self):
        ext = extensions()
        ext[0]["state"]["metadataAddress"] = "A"*44
        ext[1]["state"]["mint"] = "A"*44
        r = audit_metadata_extensions(MINT, ext)
        self.assertIn("METADATA_POINTER_ADDRESS_MISMATCH", r["reasons"])
        self.assertIn("TOKEN_METADATA_MINT_MISMATCH", r["reasons"])

    def test_unknown_extension_cannot_be_metadata_clear(self):
        ext = extensions() + [{"extension": "transferHook", "state": {}}]
        r = audit_metadata_extensions(MINT, ext)
        self.assertEqual(r["status"], "BLOCK_INCOMPLETE")
        self.assertIn("NON_METADATA_EXTENSIONS_REQUIRE_SEPARATE_AUDIT", r["reasons"])
        sec = evaluate_mint(MINT, [], account(ext))
        self.assertEqual(sec["security_gate"], "BLOCK")
        self.assertTrue(any(s.startswith("RISK_EXTENSION_") for s in sec["reasons"]))

    def test_missing_duplicate_or_malformed_entries_fail_closed(self):
        for bad in (None, [], [extensions()[0]], extensions()+[extensions()[0]],
                    [{"extension": "metadataPointer", "state": {}}, {"extension": "tokenMetadata"}]):
            with self.subTest(case=repr(bad)[:50]):
                self.assertEqual(audit_metadata_extensions(MINT, bad)["status"], "BLOCK_INCOMPLETE")

    def test_missing_authorities_and_fields_are_not_treated_as_null(self):
        ext = extensions()
        del ext[0]["state"]["authority"]
        del ext[1]["state"]["updateAuthority"]
        del ext[1]["state"]["additionalMetadata"]
        r = audit_metadata_extensions(MINT, ext)
        self.assertIn("METADATA_POINTER_AUTHORITY_ACTIVE_OR_UNKNOWN", r["reasons"])
        self.assertIn("TOKEN_METADATA_UPDATE_AUTHORITY_ACTIVE_OR_UNKNOWN", r["reasons"])
        self.assertIn("ADDITIONAL_METADATA_UNVERIFIED", r["reasons"])


if __name__ == "__main__":
    unittest.main()
