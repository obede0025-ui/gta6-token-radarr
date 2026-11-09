import unittest
from datetime import datetime,timedelta,timezone
from m1_v3_forward_monitor import bootstrap,advance
from m1_v3_2_forward_quotes import bootstrap as qboot,evaluate
from m1_v3_latency_audit import diagnose
class TimestampPipelineTests(unittest.TestCase):
 def test_new_timestamps_propagate(self):
  t=datetime(2026,11,9,18,tzinfo=timezone.utc)
  b3time=t.isoformat()
  e={"scanned_at":(t-timedelta(seconds=120)).isoformat(),"recorded_utc":b3time,
     "mint":"mint","pair":"pair","decision":"REQUIRES_FURTHER_SAFETY_AND_QUOTE_REVIEW","price_usd":1.0}
  shadow={"events":[]}
  state=bootstrap(shadow,t)
  shadow["events"]=[e]
  advance(state,shadow,t+timedelta(seconds=10),lambda m,p:{"price":1,"liquidity":23000})
  item=state["tracked"]["mint"]
  self.assertEqual(item["b3_recorded_utc"],b3time)
  self.assertEqual(item["registered_utc"],(t+timedelta(seconds=10)).isoformat())
  qstate=qboot({"version":1,"tracked":{}},t)
  evaluate(qstate,{"version":1,"tracked":state["tracked"]},t+timedelta(seconds=20),
           lambda a,b,n:{"output_raw":1000})
  pos=qstate["positions"]["mint"]
  self.assertEqual(pos["first_seen_utc"],(t+timedelta(seconds=20)).isoformat())
  result=diagnose({"events":[e]},{"tracked":state["tracked"]},{"positions":qstate["positions"]})
  row=result["observations"][0]
  self.assertEqual(row["b3_to_m1_registration_seconds"],10)
  self.assertEqual(row["m1_to_v32_first_seen_seconds"],10)
 def test_legacy_state_is_still_readable(self):
  t=datetime(2026,11,9,18,tzinfo=timezone.utc)
  old={"mint":"x","pair":"p","scanned_at":t.isoformat(),"decision":"REQUIRES_FURTHER_SAFETY_AND_QUOTE_REVIEW","price_usd":1}
  state=bootstrap({"events":[]},t)
  advance(state,{"events":[old]},t+timedelta(seconds=10),
          lambda m,p:{"price":1,"liquidity":23000})
  self.assertIsNone(state["tracked"]["x"]["b3_recorded_utc"])
if __name__=="__main__":unittest.main()
