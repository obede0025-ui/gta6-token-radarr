"""Manual quote refresh with cache. Does not persist credentials or place orders."""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--min-seconds", type=int, default=900)
    args = parser.parse_args()
    if args.min_seconds < 900:
        parser.error("minimum refresh interval is 900 seconds")
    output = Path(args.output)
    if output.exists():
        try:
            existing = json.loads(output.read_text(encoding="utf-8"))
            checked = existing.get("status") == "OK"
        except (OSError, ValueError, TypeError):
            checked = False
        if checked and time.time() - output.stat().st_mtime < args.min_seconds:
            print("CACHE_HIT: no Jupiter requests")
            return
    if not os.getenv("JUPITER_API_KEY"):
        print("NOT_CONFIGURED: no quote refresh attempted")
        return
    monitor = Path(__file__).with_name("v63_quote_monitor.py")
    command = [sys.executable, str(monitor), "--state", args.state]
    response = subprocess.run(command, capture_output=True, text=True, timeout=240, check=True)
    data = json.loads(response.stdout)
    # Never treat incomplete quotes as fully successful.
    if data.get("status") != "OK":
        print("QUOTE_STATUS_" + str(data.get("status")) + ": not cached")
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_name(output.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.replace(output)
    print("QUOTE_REFRESH_OK")

if __name__ == "__main__":
    main()
