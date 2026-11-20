import unittest
from research_pumpswap_vault_candidates import classify_report


class VaultCandidateTests(unittest.TestCase):
    def test_owner_match_requires_success(self):
        row={"token_account":"VAULT","mint":"TOKEN","owner_reported":"POOL","delta_raw":"-10"}
        report={"cases":[{"case":"A","pool":"POOL","transactions":[
            {"available":True,"transaction_error":None,"token_account_deltas":[row]},
            {"available":True,"transaction_error":{"InstructionError":[0,1]},
             "token_account_deltas":[row]},
            {"available":False,"error":"429"},
            {"available":True,"transaction_error":None,
             "token_account_deltas":[dict(row,owner_reported="OTHER")]}
        ]}]}
        c=classify_report(report)["cases"][0]
        self.assertEqual(c["successful_transactions"],2)
        self.assertEqual(c["failed_transactions"],1)
        self.assertEqual(c["unavailable_transactions"],1)
        self.assertEqual(len(c["pool_owned_token_account_candidates"]),1)
        self.assertEqual(c["pool_owned_token_account_candidates"][0]["sampled_successful_deltas"],1)
        self.assertFalse(c["historical_pool_reserves_verified"])

    def test_empty_report_stays_unverified(self):
        output=classify_report({"cases":[{"case":"B","pool":"P","transactions":[]}]})
        self.assertEqual(output["cases"][0]["classification"],"UNVERIFIED")
        self.assertEqual(output["database_writes"],0)
        self.assertEqual(output["trades_executed"],0)


if __name__=="__main__":
    unittest.main()
