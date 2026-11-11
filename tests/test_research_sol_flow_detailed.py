"""Offline unit tests for read-only Solana SOL flow decoding."""
import unittest

from research_sol_flow_detailed import balance_changes, parse_trade, summarize

MINT = "3Ufq2s3yLx6Tu7DWeTuCDjjASJrDGrojKUBnfUknpump"
POOL = "2Q1BzemocMGLH5VHvxoKczdgY2qUCC1StwDTHuwQM1cL"
WALLET = "7X1BuyerTestWallet"


def tx(side="Sell", pool_before=100, pool_after=120,
       owner_before=50, owner_after=30,
       sol_before=1000000000, sol_after=1200000000):
    return {
        "meta": {
            "err": None,
            "logMessages": ["Program log: Instruction: " + side],
            "preTokenBalances": [
                {"accountIndex": 2, "owner": POOL, "mint": MINT,
                 "uiTokenAmount": {"uiAmountString": str(pool_before)}},
                {"accountIndex": 3, "owner": WALLET, "mint": MINT,
                 "uiTokenAmount": {"uiAmountString": str(owner_before)}},
            ],
            "postTokenBalances": [
                {"accountIndex": 2, "owner": POOL, "mint": MINT,
                 "uiTokenAmount": {"uiAmountString": str(pool_after)}},
                {"accountIndex": 3, "owner": WALLET, "mint": MINT,
                 "uiTokenAmount": {"uiAmountString": str(owner_after)}},
            ],
            "preBalances": [sol_before, 0, 0, 0],
            "postBalances": [sol_after, 0, 0, 0],
        },
        "transaction": {"message": {"accountKeys": [
            {"pubkey": WALLET, "signer": True},
            {"pubkey": "another-account", "signer": False},
            {"pubkey": POOL, "signer": False},
            {"pubkey": "token-account", "signer": False},
        ]}},
    }


class SolFlowTests(unittest.TestCase):
    def test_sell_native_SOL_receipt_approx(self):
        rec = parse_trade(tx(), MINT, POOL)
        self.assertEqual(rec["side"], "SELL")
        self.assertEqual(rec["tokens"], 20)
        self.assertAlmostEqual(rec["sol"], 0.2)
        self.assertEqual(rec["sol_method"], "WALLET_SOL_NET_APPROX")

    def test_buy_native_sol_spent_approx(self):
        rec = parse_trade(tx(side="Buy", pool_before=120, pool_after=100,
                             owner_before=30, owner_after=50,
                             sol_before=1200000000, sol_after=1000000000),
                          MINT, POOL)
        self.assertEqual(rec["side"], "BUY")
        self.assertEqual(rec["tokens"], 20)
        self.assertAlmostEqual(rec["sol"], 0.2)

    def test_missing_sol_receipt_explicitly_unknown(self):
        rec = parse_trade(tx(sol_before=1000000000,
                             sol_after=999995000), MINT, POOL)
        self.assertEqual(rec["side"], "SELL")
        self.assertIsNone(rec["sol"])
        self.assertEqual(rec["sol_method"], "UNKNOWN")

    def test_summary_never_turns_missing_sol_into_observation(self):
        row = {"side": "SELL", "period": "BEFORE", "sol": None,
               "tokens": 20, "wallets": [WALLET], "lp_hints": []}
        summary = summarize([row])["BEFORE"]
        self.assertEqual(summary["sell_sol_known_n"], 0)
        self.assertEqual(summary["sell_sol_unknown_n"], 1)

    def test_failed_tx_not_counted_as_sell(self):
        item = tx()
        item["meta"]["err"] = {"InstructionError": [2, "Custom"]}
        self.assertEqual(parse_trade(item, MINT, POOL)["side"], "FAILED")


if __name__ == "__main__":
    unittest.main()
