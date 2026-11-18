import json
import tempfile
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch

from research_quote_cohort_v33 import (
    new_state, load_state, due_followups, add_new, run
)

NOW=datetime(2026,11,18,21,0,tzinfo=timezone.utc)
MINT="A"*44
PAIR="B"*44

def candidate(scanned=NOW):
    return {"mint":MINT,"pair":PAIR,"strategy":"B",
        "scanned_at_utc":scanned.isoformat(timespec="seconds"),
        "liquidity_usd":"22000","volume_5m_usd":"500","buys_m5":7,
        "sells_m5":2,"pair_age_hours":2}

class FakeQuote:
    def __init__(self,throw=False):self.calls=[];self.throw=throw
    def quote(self,src,dst,amount):
        self.calls.append((src,dst,amount))
        if self.throw:raise ValueError("unavailable")
        return 9_500_000

class TestCohort(unittest.TestCase):
    def test_empty_and_corrupt_ledger_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"ledger.json"
            self.assertEqual(load_state(p,NOW)["signals"],{})
            p.write_text('{"version":999,"signals":{}}')
            with self.assertRaises(ValueError):load_state(p,NOW)

    def test_due_horizons_do_not_replay_future(self):
        state=new_state(NOW)
        key=MINT+"|"+PAIR
        state["signals"][key]={"first_seen_utc":NOW.isoformat(),
                              "buy_raw_out":"10000","mint":MINT,
                              "followups":{}}
        self.assertEqual(due_followups(state["signals"],NOW+timedelta(minutes=3)),[])
        self.assertEqual(len(due_followups(state["signals"],NOW+timedelta(minutes=6))),1)
        state["signals"][key]["followups"]["300"]={"status":"HORIZON_MISSED"}
        self.assertEqual(len(due_followups(state["signals"],NOW+timedelta(minutes=16))),1)

    def test_stale_candidate_does_not_use_network(self):
        state=new_state(NOW)
        q=FakeQuote()
        with patch("research_quote_cohort_v33.probe_candidate") as probe:
            added=add_new(state,[candidate(NOW-timedelta(minutes=4))],NOW,q,object(),"https://rpc")
        self.assertEqual(added,[])
        probe.assert_not_called()

    def test_repeated_candidate_not_counted_twice(self):
        state=new_state(NOW)
        key=MINT+"|"+PAIR
        state["signals"][key]={"first_seen_utc":NOW.isoformat(),"followups":{}}
        with patch("research_quote_cohort_v33.probe_candidate") as probe:
            self.assertEqual(add_new(state,[candidate()],NOW,FakeQuote(),object(),"https://rpc"),[])
            probe.assert_not_called()

    def test_new_quote_is_observation_not_fill(self):
        state=new_state(NOW)
        route={"buy_status":"OK","sell_status":"OK",
               "buy_raw_out":120000,"immediate_sell_usdc":"9.7"}
        security={"security_gate":"BLOCK"}
        verdict={"evidence_state":"BLOCKED","reasons":["TOKEN_SECURITY_NOT_CLEARED"]}
        with patch("research_quote_cohort_v33.probe_candidate",return_value=(verdict,route,security)):
            added=add_new(state,[candidate()],NOW,FakeQuote(),object(),"https://rpc")
        self.assertEqual(len(added),1)
        item=state["signals"][added[0]]
        self.assertEqual(item["buy_raw_out"],"120000")
        self.assertEqual(item["fills_verified"],0)
        self.assertEqual(item["paper_trade_approval"],"BLOCK")
        self.assertEqual(item["live_trade_approval"],"BLOCK")
        self.assertEqual(state["paper_orders_executed"],0)

    def test_run_persists_only_dedicated_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            db=root/"input.db"; db.write_bytes(b"readonly sentry")
            portfolio=root/"portfolio.json"; portfolio.write_text('{"original":true}')
            ledger=root/"v33"/"ledger.json"
            report=root/"v33"/"report.json"
            before=(db.read_bytes(),portfolio.read_bytes())
            with patch("research_quote_cohort_v33.selection",return_value=[]):
                output=run(db,portfolio,ledger,report,NOW)
            self.assertEqual(output["new_count"],0)
            self.assertTrue(ledger.is_file())
            self.assertTrue(report.is_file())
            self.assertEqual((db.read_bytes(),portfolio.read_bytes()),before)
            self.assertEqual(json.loads(ledger.read_text())["signals"],{})
            self.assertEqual(output["live_orders_executed"],0)

    def test_paths_must_not_alias_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"same"
            with self.assertRaises(ValueError):
                run(p,p,p,p,NOW)

if __name__=="__main__":unittest.main()
