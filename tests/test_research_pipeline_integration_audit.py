import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from research_pipeline_integration_audit import run

class Tests(unittest.TestCase):
    def test_minimal_states_no_write(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            (root/"gta6-v63-test").mkdir()
            (root/"gta6-v32-gate-pilot").mkdir()
            (root/"gta6-paper-vps/data").mkdir(parents=True)
            dbpath=root/"gta6-paper-vps/data/learning.db"
            with sqlite3.connect(dbpath) as db:
                db.execute("CREATE TABLE snapshots (scanned_at TEXT)")
                db.execute("INSERT INTO snapshots VALUES ('2026-11-12T14:00:00+00:00')")
            original={
                "gta6-v63-test/b3_state.json":{"events":[]},
                "gta6-v32-gate-pilot/b3_30s_state.json":{"events":[]},
                "gta6-v63-test/m1_v3_state.json":{"tracked":{}},
                "gta6-v63-test/m1_v3_2_state.json":{"positions":{}},
                "gta6-v32-gate-pilot/volume_3000_shadow_state.json":{"candidates":{},"started_utc":"2026-11-12T14:00:00+00:00"},
                "gta6-v32-gate-pilot/paper_pipeline_v1_state.json":{"candidates":{},"started_utc":"2026-11-12T14:00:00+00:00"},
            }
            for path,value in original.items():
                (root/path).write_text(json.dumps(value))
            result=run(datetime(2026,11,12,14,1,tzinfo=timezone.utc),root)
            self.assertEqual(result["summary"].get("FAIL",0),0)
            self.assertEqual(result["summary"]["GAP"],2)
            for path,value in original.items():
                self.assertEqual(json.loads((root/path).read_text()),value)

if __name__=="__main__":
    unittest.main()
