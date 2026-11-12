import unittest
from datetime import datetime,timedelta,timezone
from research_b3_minute_momentum_validation import select_causal,momentum,analyze

def t(n):
    return (datetime(2026,11,12,14,0,tzinfo=timezone.utc)+timedelta(minutes=n)).isoformat()

def row(n,price=1.,liq=30000,status="QUOTE_MARKET_OBSERVED",vol=3000):
    return (t(n),"mint","pair","source",status,price,liq,vol,20,10,1)

class MomentumTests(unittest.TestCase):
    def test_legacy_minute_gaps_fail_spaced_succeeds(self):
        history=[(t(i),"QUOTE_MARKET_OBSERVED",1.,3000) for i in range(10)]
        current=(t(10),"QUOTE_MARKET_OBSERVED",1.06,3000)
        self.assertIsNone(select_causal(history,current,"raw"))
        chosen=select_causal(history,current,"spaced")
        self.assertIsNotNone(chosen)
        self.assertEqual([x[0] for x in chosen],[t(2),t(6)])
        self.assertTrue(momentum(current,chosen))

    def test_no_future_lookahead(self):
        history=[(t(i),"QUOTE_MARKET_OBSERVED",1.,3000) for i in (0,4,8,20)]
        current=(t(12),"QUOTE_MARKET_OBSERVED",1.06,3000)
        chosen=select_causal(history,current,"spaced")
        self.assertEqual([x[0] for x in chosen],[t(4),t(8)])

    def test_unknown_liquidity_cannot_be_signal(self):
        rows=[row(i,1.0) for i in range(9)]
        rows.append(row(9,1.1,None,"LIQUIDITY_UNKNOWN"))
        result=analyze(rows)
        self.assertEqual(result["summary"].get("base_plus_spaced_momentum_RESEARCH_ONLY",0),0)
        self.assertEqual(result["summary"]["unknown_liquidity"],1)

    def test_forward_causal_baseline(self):
        rows=[row(i,1.) for i in range(10)]
        rows.append(row(10,1.1))
        result=analyze(rows)
        self.assertGreaterEqual(result["summary"].get("spaced_sampling_ready",0),1)
        self.assertGreaterEqual(result["summary"].get("base_plus_spaced_momentum_RESEARCH_ONLY",0),1)
        self.assertEqual(result["summary"].get("legacy_spacing_ready",0),0)

if __name__=="__main__":unittest.main()
