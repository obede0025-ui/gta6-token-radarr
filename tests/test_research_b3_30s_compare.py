import unittest
from research_b3_30s_compare import compare


class CompareTests(unittest.TestCase):
    def test_same_snapshot_matching_and_decisions(self):
        row = dict(scanned_at="2026-11-12T12:00:00+00:00",
                   mint="abc", pair="xyz", decision="BLOCKED", reasons=["LOW"])
        orig = dict(row, recorded_utc="2026-11-12T12:03:00+00:00")
        pilot = dict(row, recorded_utc="2026-11-12T12:00:20+00:00")
        r = compare({"events": [orig]}, {"events": [pilot]})
        self.assertEqual(r["counts"]["matched_same_snapshot"], 1)
        self.assertEqual(r["counts"]["decision_disagreements"], 0)
        self.assertEqual(r["paired_timing"]["saving_existing_minus_pilot"]["median_seconds"], 160)

    def test_disagreement_detected(self):
        base = dict(scanned_at="2026-11-12T12:00:00+00:00",
                    recorded_utc="2026-11-12T12:00:02+00:00",
                    mint="abc", pair="xyz")
        r = compare({"events": [dict(base, decision="BLOCKED", reasons=["LOW"])]},
                    {"events": [dict(base, decision="REQUIRES_FURTHER_SAFETY_AND_QUOTE_REVIEW",
                                      reasons=[])]})
        self.assertEqual(r["counts"]["decision_disagreements"], 1)


if __name__ == "__main__":
    unittest.main()
