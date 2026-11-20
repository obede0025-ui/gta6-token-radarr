"""Regression checks: canonical Token-2022 mint program is identified, not approved."""
import unittest
from radar_token_security import TOKEN_2022, evaluate_mint

CANONICAL = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
MINT = "pcDL74F9AYQ8ConwemryWMHQX5LWbPHyAEbULGNpump"


def token2022_account(owner=CANONICAL):
    return {"owner": owner, "executable": False,
            "data": {"parsed": {"type": "mint", "info": {
                "mintAuthority": None, "freezeAuthority": None,
                "decimals": 6, "supply": "1000000",
                "extensions": [
                    {"extension": "metadataPointer", "state": {"authority": None, "metadataAddress": MINT}},
                    {"extension": "tokenMetadata", "state": {"mint": MINT, "updateAuthority": None,
                      "additionalMetadata": []}}
                ]}}}}


class Token2022ProgramIdTests(unittest.TestCase):
    def test_canonical_program_is_recognized_but_fail_closed(self):
        self.assertEqual(TOKEN_2022, CANONICAL)
        result = evaluate_mint(MINT, ["test"], token2022_account())
        self.assertEqual(result["program"], "TOKEN_2022")
        self.assertEqual(result["mint_authority"], None)
        self.assertEqual(result["freeze_authority"], None)
        self.assertEqual(result["extensions"], ["metadataPointer", "tokenMetadata"])
        self.assertEqual(result["security_gate"], "BLOCK")
        self.assertEqual(result["live_trade_approval"], "BLOCK")
        self.assertIn("TOKEN_2022_REQUIRES_EXTENSION_AUDIT", result["reasons"])

    def test_unknown_program_cannot_be_accepted(self):
        result = evaluate_mint(MINT, ["test"], token2022_account("A"*44))
        self.assertEqual(result["program"], "UNKNOWN")
        self.assertEqual(result["security_gate"], "BLOCK")
        self.assertIn("UNKNOWN_PROGRAM_OWNER", result["reasons"])


if __name__ == "__main__":
    unittest.main()
