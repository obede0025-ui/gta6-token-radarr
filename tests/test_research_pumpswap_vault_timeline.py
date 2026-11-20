"""Offline tests for bounded PumpSwap vault timeline."""
import base64
import unittest
from research_pumpswap_official_vault_match import DISCRIMINATOR, PROGRAM, b58
from research_pumpswap_vault_timeline import summarize_case


class VaultTimelineTests(unittest.TestCase):
    def sample(self):
        fields=[bytes([x])*32 for x in range(1,7)]
        data=DISCRIMINATOR+b"\x01\x00\x00"+b"".join(fields)
        pool={"owner":PROGRAM,"data":[base64.b64encode(data).decode(),"base64"]}
        case={"label":"TEST","pair":"POOL","mint":b58(fields[4-3]),
              "zero_utc":"2026-11-20T10:01:00+00:00"}
        # fields[1] is base mint, fields[4] is base vault
        return fields,pool,case

    def test_successful_vault_delta_and_within_slot_ambiguity(self):
        fields,pool,case=self.sample()
        sigs=[{"signature":"A","blockTime":1795168800},
              {"signature":"B","blockTime":1795168920}]
        def tx(signature):
            return {"slot":99,"blockTime":1795168800,
              "transaction":{"message":{"accountKeys":[
                  {"pubkey":b58(fields[4])}]}},
              "meta":{"err":None,
                "preTokenBalances":[{"accountIndex":0,"mint":b58(fields[1]),
                    "uiTokenAmount":{"amount":"100","decimals":6}}],
                "postTokenBalances":[{"accountIndex":0,"mint":b58(fields[1]),
                    "uiTokenAmount":{"amount":"1","decimals":6}}]}}
        result=summarize_case(case,pool,sigs,{"A":tx("A"),"B":tx("B")},1)
        self.assertEqual(result["transactions_with_vault_changes"],2)
        self.assertTrue(all(x["within_slot_order_unknown"]
                            for x in result["vault_balance_events"]))
        self.assertFalse(result["historical_reserves_fully_verified"])

    def test_missing_transaction_does_not_create_zero(self):
        fields,pool,case=self.sample()
        sigs=[{"signature":"A","blockTime":1795168800},
              {"signature":"B","blockTime":1795168920}]
        result=summarize_case(case,pool,sigs,{},1)
        self.assertEqual(result["missing_transactions"],2)
        self.assertEqual(result["transactions_with_vault_changes"],0)
        self.assertEqual(result["state"],"UNVERIFIED")

    def test_wrong_mint_fails_closed(self):
        fields,pool,case=self.sample()
        case["mint"]="unrelated"
        output=summarize_case(case,pool,[],{},1)
        self.assertFalse(output["mint_matches_pool"])
        self.assertEqual(output["vault_balance_events"],[])


if __name__=="__main__":
    unittest.main()
