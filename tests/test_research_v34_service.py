import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]/"deploy"/"systemd"

class ServiceSafetyTests(unittest.TestCase):
    def test_isolated_research_service(self):
        data=(ROOT/"gta6-v34-research.service").read_text()
        self.assertIn("User=ubuntu",data)
        self.assertIn("research_v34_cycle.py",data)
        self.assertIn("--output /home/ubuntu/gta6-v32-gate-pilot/data/v33",data)
        self.assertIn("--portfolio /home/ubuntu/gta6-paper-vps/data/v591/portfolio.json",data)
        self.assertNotIn("wallet",data.split("ExecStart=",1)[1].splitlines()[0].lower())
        self.assertNotIn("swap",data.split("ExecStart=",1)[1].splitlines()[0].lower())
        self.assertNotIn("privatekey",data.split("ExecStart=",1)[1].splitlines()[0].lower())

    def test_timer_requires_manual_enable_and_no_persistent_catchup(self):
        data=(ROOT/"gta6-v34-research.timer").read_text()
        self.assertIn("Persistent=false",data)
        self.assertIn("OnUnitInactiveSec=",data)
        self.assertIn("gta6-v34-research.service",data)
        self.assertNotIn("OnCalendar=",data)

if __name__=="__main__":unittest.main()
