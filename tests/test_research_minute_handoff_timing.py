import unittest
from research_minute_handoff_timing import analyze, parse_journal

def line(time, message):
    return f"2026-11-16T{time}+00:00 vps-test systemd[1]: {message}\n"

B3="gta6-b3-minute-safety-shadow.service"
M1="gta6-m1-minute-research.service"

class TimingAuditTests(unittest.TestCase):
    def test_observed_phase_gap(self):
        rows=[
            line("14:07:50",f"Starting {B3} - observer..."),
            line("14:07:51",f"Finished {B3} - observer."),
            line("14:08:22",f"Starting {M1} - observer..."),
            line("14:08:22",f"Finished {M1} - observer."),
            line("14:08:53",f"Starting {B3} - observer..."),
            line("14:08:53",f"Finished {B3} - observer."),
            line("14:09:24",f"Starting {M1} - observer..."),
            line("14:09:24",f"Finished {M1} - observer."),
        ]
        result=analyze(rows)
        self.assertEqual(result["paired_cycles"],2)
        self.assertEqual(result["observed_b3_finish_to_m1_start"]["median_s"],31)
        self.assertEqual(result["counterfactual_immediate_handoff"]["assumed_trigger_delay_seconds"],0)

    def test_does_not_pair_m1_before_b3_finish(self):
        rows=[
            line("14:01:00",f"Starting {M1} - followup..."),
            line("14:01:00",f"Finished {M1} - followup."),
            line("14:01:01",f"Starting {B3} - observer..."),
            line("14:01:02",f"Finished {B3} - observer."),
            line("14:02:00",f"Starting {B3} - observer..."),
            line("14:02:01",f"Finished {B3} - observer."),
        ]
        r=analyze(rows)
        self.assertEqual(r["paired_cycles"],0)
        self.assertEqual(r["unpaired_b3_cycles"],2)

    def test_incomplete_journal_not_assumed_success(self):
        rows=[line("14:00:00",f"Starting {B3} - observer...")]
        r=analyze(rows)
        self.assertEqual(r["b3_complete_runs"],0)
        self.assertEqual(r["incomplete_or_duplicate_events"]["b3"],1)

    def test_no_app_json_false_positive(self):
        rows=['2026-11-16T14:01:00+00:00 vps python[123]: {"mode":"MINUTE_B3_SAFETY_SHADOW"}',
              "Hint: journal access is limited"]
        self.assertEqual(parse_journal(rows),[])
        self.assertEqual(analyze(rows)["paired_cycles"],0)

if __name__=="__main__":
    unittest.main()
