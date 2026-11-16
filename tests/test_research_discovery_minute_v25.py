import tempfile
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path

from research_discovery_minute_v25 import run

class Clock:
    def __init__(self):
        self.seconds=0.
        self.starts=[]
    def monotonic(self):return self.seconds
    def sleep(self,n):self.seconds+=n
    def utc(self):
        return datetime(2026,11,16,18,0,tzinfo=timezone.utc)+timedelta(seconds=self.seconds)

class CollectorTests(unittest.TestCase):
    def test_bounded_schedule_and_output(self):
        clock=Clock()
        def sample(shadow,now,slots):
            clock.starts.append(clock.seconds)
            return {"status":"RECORDED","observed_utc":now.isoformat(timespec="seconds"),
                "feed_order":{"selected":19,"known_liquidity":8,"base_pass_including_pair_age":2,
                              "spaced_momentum_pass":0},
                "activity_first":{"selected":20,"known_liquidity":10,"base_pass_including_pair_age":3,
                                  "spaced_momentum_pass":1},
                "new_unique_signals":{"feed_order":0,"activity_first":1}}
        with tempfile.TemporaryDirectory() as tmp:
            report=run(path=Path(tmp)/"missing.db",cycles=3,interval=60,
                sample_fn=sample,sleep_fn=clock.sleep,monotonic_fn=clock.monotonic,
                utcnow_fn=clock.utc)
        self.assertEqual(clock.starts,[0.,60.,120.])
        self.assertEqual(report["recorded_cycles"],3)
        self.assertEqual(report["cohorts"]["activity_first"]["momentum_pass_observations"],3)
        self.assertEqual(report["gaps_over_600_seconds"],0)

    def test_no_catchup_burst_after_slow_call(self):
        clock=Clock()
        def sample(shadow,now,slots):
            clock.starts.append(clock.seconds)
            clock.seconds+=75
            return {"status":"DUPLICATE_SECOND_SKIPPED"}
        with tempfile.TemporaryDirectory() as tmp:
            report=run(path=Path(tmp)/"missing.db",cycles=3,interval=60,
                sample_fn=sample,sleep_fn=clock.sleep,monotonic_fn=clock.monotonic,
                utcnow_fn=clock.utc)
        self.assertEqual(clock.starts,[0,135,270])
        self.assertEqual(report["recorded_cycles"],0)

    def test_stops_after_three_errors(self):
        clock=Clock()
        def sample(shadow,now,slots):raise ConnectionError("remote service offline")
        with tempfile.TemporaryDirectory() as tmp:
            report=run(path=Path(tmp)/"missing.db",cycles=10,interval=60,
                sample_fn=sample,sleep_fn=clock.sleep,monotonic_fn=clock.monotonic,
                utcnow_fn=clock.utc)
        self.assertEqual(report["attempted"],3)
        self.assertEqual(len(report["errors"]),3)

    def test_interval_and_cycle_limits(self):
        with self.assertRaises(ValueError):run(cycles=31)
        with self.assertRaises(ValueError):run(interval=30)
        with self.assertRaises(ValueError):run(slots=21)

if __name__=="__main__":unittest.main()
