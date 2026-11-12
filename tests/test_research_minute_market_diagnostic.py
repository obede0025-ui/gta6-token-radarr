import unittest
from research_minute_market_diagnostic import market_classification

class Diagnostics(unittest.TestCase):
    def test_exact_pair_and_alternate(self):
        wanted={"mint1":"pair1","mint2":"pair2","mint3":"pair3"}
        payload=[
            {"chainId":"solana","baseToken":{"address":"mint1"},"pairAddress":"different","priceUsd":"1"},
            {"chainId":"solana","baseToken":{"address":"mint2"},"pairAddress":"pair2","priceUsd":"2","liquidity":{"usd":100}},
        ]
        result=market_classification(wanted,payload)
        self.assertEqual(result["mint1"],"ALTERNATE_PAIR_ONLY")
        self.assertEqual(result["mint2"],"EXACT_PAIR_VALID")
        self.assertEqual(result["mint3"],"NO_SOLANA_PAIR_RETURNED")

    def test_invalid_exact(self):
        got=market_classification({"mint":"pair"},[
            {"chainId":"solana","baseToken":{"address":"mint"},"pairAddress":"pair",
             "priceUsd":None,"liquidity":{"usd":0}}
        ])
        self.assertEqual(got["mint"],"EXACT_PAIR_INVALID_MARKET")

if __name__=="__main__":unittest.main()
