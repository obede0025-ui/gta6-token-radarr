import unittest
from datetime import datetime,timezone
from decimal import Decimal
from unittest.mock import patch
from m1_v3_1_quote_audit import candidates, audit_one, run, COOLDOWN_SECONDS

MINT="So11111111111111111111111111111111111111112"
class M1V31Tests(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,11,9,15,tzinfo=timezone.utc)
        self.state={"version":1,"tracked":{
            MINT:{"mint":MINT,"status":"WATCHING","signal":self.now.isoformat()},
            "old":{"mint":"old","status":"WINDOW_ENDED","signal":self.now.isoformat()}}}
    def test_only_active_and_triggered(self):
        self.assertEqual(len(candidates(self.state)),1)
    def test_quote_both_ways(self):
        with patch("m1_v3_1_quote_audit.quote",
                   side_effect=[{"output_raw":100000000,"router":"A"},
                                {"output_raw":24000000,"router":"B"}]) as q:
            out=audit_one(self.state["tracked"][MINT],None,"TEST")
        self.assertEqual(out["status"],"QUOTE_AVAILABLE")
        self.assertEqual(out["roundtrip_change_pct"],"-4.0000")
        self.assertEqual(q.call_count,2)
        self.assertEqual(q.call_args_list[0].args[2], "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v")
        self.assertEqual(q.call_args_list[1].args[2], MINT)
    def test_cooldown(self):
        previous={"mode":"M1_V3_1_QUOTE_ONLY","checked_utc":self.now.isoformat(),
                  "tokens":[{"mint":MINT}]}
        result=run(self.state,previous,now=self.now,session=None,api_key="TEST")
        self.assertEqual(result["tokens"],[])
        self.assertEqual(result["skipped_recent"],1)
    def test_missing_key_fails_before_request(self):
        with self.assertRaises(ValueError):
            run(self.state,None,now=self.now,session=None,api_key="")
    def test_no_live_candidates(self):
        self.state["tracked"][MINT]["status"]="WINDOW_ENDED"
        r=run(self.state,None,now=self.now,session=None,api_key="TEST")
        self.assertEqual(r["tokens"],[])
if __name__=="__main__":unittest.main()
