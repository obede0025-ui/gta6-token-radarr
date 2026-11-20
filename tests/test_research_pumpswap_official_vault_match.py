import base64
import unittest
from research_pumpswap_official_vault_match import (
    decode_pool, analyze, PROGRAM, DISCRIMINATOR, b58
)


class OfficialVaultTests(unittest.TestCase):
    def test_layout_offsets_and_match(self):
        # Distinct 32-byte public keys to expose accidental offset shifts.
        vals=[bytes([i])*32 for i in range(1,7)]
        raw=DISCRIMINATOR + b"\x01" + b"\x00\x00" + b"".join(vals)
        account={"owner":PROGRAM,"data":[base64.b64encode(raw).decode(),"base64"]}
        pool=decode_pool(account,"POOL")
        self.assertEqual(pool["status"],"POOL_LAYOUT_VERIFIED")
        self.assertEqual(pool["base_mint"],b58(vals[1]))
        self.assertEqual(pool["quote_mint"],b58(vals[2]))
        self.assertEqual(pool["base_vault"],b58(vals[4]))
        self.assertEqual(pool["quote_vault"],b58(vals[5]))
        report={"cases":[{"case":"A","pool":"POOL","mint":b58(vals[1]),
            "transactions":[{"signature":"T","available":True,
                "transaction_error":None,"slot":100,
                "token_account_deltas":[{"token_account":b58(vals[4]),
                    "mint":b58(vals[1]),"pre_raw":"100",
                    "post_raw":"1","delta_raw":"-99"}]}]}]}
        result=analyze(report,{"POOL":pool})["cases"][0]
        self.assertEqual(len(result["verified_vault_addresses_in_successful_transactions"]),1)
        self.assertEqual(result["classification"],"UNVERIFIED")
        self.assertFalse(result["historical_reserves_fully_verified"])

    def test_wrong_owner_and_discriminator_fail_closed(self):
        raw=b"\x00"*203
        account={"owner":PROGRAM,"data":[base64.b64encode(raw).decode(),"base64"]}
        self.assertEqual(decode_pool(account,"P")["status"],"LAYOUT_OR_DISCRIMINATOR_MISMATCH")
        account["owner"]="other"
        self.assertEqual(decode_pool(account,"P")["status"],"WRONG_OWNER")

    def test_mismatch_reports_decoded_mints_but_no_vault_confirmation(self):
        report={"cases":[{"case":"A","pool":"P","mint":"SOME_OTHER_TOKEN",
            "transactions":[{"signature":"T","available":True,
                "transaction_error":None,"token_account_deltas":[
                    {"token_account":"BASE_V","mint":"BASE","delta_raw":"-2"}]}]}]}
        pool={"status":"POOL_LAYOUT_VERIFIED","base_mint":"BASE",
              "quote_mint":"QUOTE","base_vault":"BASE_V","quote_vault":"QUOTE_V"}
        case=analyze(report,{"P":pool})["cases"][0]
        self.assertEqual(case["decoded_base_mint"],"BASE")
        self.assertEqual(case["decoded_quote_mint"],"QUOTE")
        self.assertFalse(case["mint_matches_either_pool_side"])
        self.assertEqual(case["verified_vault_addresses_in_successful_transactions"],[])
        self.assertEqual(case["classification"],"UNVERIFIED")

    def test_quote_side_mint_can_match(self):
        report={"cases":[{"case":"A","pool":"P","mint":"QUOTE",
            "transactions":[{"signature":"T","available":True,
                "transaction_error":None,"token_account_deltas":[
                    {"token_account":"QUOTE_V","mint":"QUOTE","delta_raw":"-2"}]}]}]}
        pool={"status":"POOL_LAYOUT_VERIFIED","base_mint":"BASE",
              "quote_mint":"QUOTE","base_vault":"BASE_V","quote_vault":"QUOTE_V"}
        case=analyze(report,{"P":pool})["cases"][0]
        self.assertTrue(case["pool_quote_mint_matches_case"])
        self.assertEqual(len(case["verified_vault_addresses_in_successful_transactions"]),1)
        self.assertFalse(case["historical_reserves_fully_verified"])

    def test_failed_transaction_not_used(self):
        report={"cases":[{"case":"A","pool":"P","mint":"M",
            "transactions":[{"available":True,
                "transaction_error":{"InstructionError":[1,1]},
                "token_account_deltas":[{"token_account":"BASE","mint":"M"}]}]}]}
        pool={"status":"POOL_LAYOUT_VERIFIED","base_mint":"M",
              "base_vault":"BASE","quote_mint":"SOL","quote_vault":"QUOTE"}
        case=analyze(report,{"P":pool})["cases"][0]
        self.assertEqual(case["verified_vault_addresses_in_successful_transactions"],[])
        self.assertEqual(case["failed_transactions"],1)


if __name__=="__main__":
    unittest.main()
