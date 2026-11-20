"""Offline transaction-meta parsing tests; never connects to Solana."""
import unittest
from research_pumpswap_transaction_meta import (
    balance_map, tx_summary, select_signatures
)


class PumpSwapMetaTests(unittest.TestCase):
    def test_exact_integer_delta_no_pool_attribution(self):
        tx={
            "slot":123,"blockTime":1794925313,
            "transaction":{"message":{"accountKeys":[
                {"pubkey":"pool"},{"pubkey":"token_account_A"}]}},
            "meta":{"err":None,
                "preTokenBalances":[{"accountIndex":1,"mint":"mint_A",
                    "uiTokenAmount":{"amount":"1000000000","decimals":9},
                    "owner":"owner_A"}],
                "postTokenBalances":[{"accountIndex":1,"mint":"mint_A",
                    "uiTokenAmount":{"amount":"400000000","decimals":9},
                    "owner":"owner_A"}]
            }
        }
        report=tx_summary("signature",tx)
        self.assertEqual(report["token_account_deltas"][0]["delta_raw"],"-600000000")
        self.assertFalse(report["token_account_deltas"][0]["verified_as_pumpswap_pool_vault"])
        self.assertFalse(report["historical_pool_reserves_verified"])

    def test_missing_metadata_never_filled_with_zero(self):
        tx={"transaction":{"message":{"accountKeys":["x"]}},
            "meta":{"preTokenBalances":[{"accountIndex":0,"mint":"m",
                "uiTokenAmount":{"amount":"100","decimals":0}}],
                "postTokenBalances":[]}}
        self.assertEqual(tx_summary("sig",tx)["token_account_deltas"],[])
        self.assertFalse(tx_summary("sig",None)["available"])

    def test_before_and_after_selected(self):
        values=[{"blockTime":98,"signature":"before"},
                {"blockTime":102,"signature":"after"},
                {"blockTime":25,"signature":"old"}]
        selected=select_signatures(values,100,each_side=1)
        self.assertEqual([x["signature"] for x in selected],["before","after"])


if __name__=="__main__":
    unittest.main()
