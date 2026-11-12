"""Read-only exploratory crash-risk audit. No orders, DB writes, or state changes."""
import argparse
import math
import sqlite3
from datetime import datetime, timezone
from statistics import mean, median

DEFAULT_DB = "/home/ubuntu/gta6-paper-vps/data/learning.db"

def parse_time(value):
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)

def load(db_path):
    uri = "file:" + db_path + "?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=30) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute("""
            SELECT s.token_address AS mint, s.pair_address AS pair,
                   s.scanned_at AS stamp, s.liquidity_usd AS liq,
                   s.volume_m5 AS vol, s.buys_m5 AS buys, s.sells_m5 AS sells,
                   o.return_pct AS ret, o.crash_30pct AS crash,
                   o.liquidity_drop_50pct AS liq_crash
            FROM snapshots s JOIN outcomes o
              ON o.token_address = s.token_address
             AND o.pair_address = s.pair_address
             AND o.baseline_at = s.scanned_at
            WHERE o.horizon_minutes = 15
              AND s.price_usd > 0
              AND s.liquidity_usd >= 20000
              AND s.volume_m5 >= 250
              AND s.buys_m5 > s.sells_m5
              AND s.pair_age_hours BETWEEN 0 AND 24
              AND o.return_pct IS NOT NULL
            ORDER BY s.scanned_at, s.token_address
        """).fetchall()
        first = {}
        for row in rows:
            first.setdefault(row["mint"], dict(row))
        examples = sorted(first.values(), key=lambda r: (r["stamp"], r["mint"]))
        for row in examples:
            # Before signal ONLY, same token and pair; cap lookback at 20m.
            from datetime import timedelta
            lower = (parse_time(row["stamp"]) - timedelta(minutes=20)).isoformat(timespec="seconds")
            prior = db.execute("""
                SELECT scanned_at, liquidity_usd
                FROM snapshots
                WHERE token_address = ? AND pair_address = ?
                  AND scanned_at < ? AND scanned_at >= ?
                  AND liquidity_usd > 0
                ORDER BY scanned_at DESC LIMIT 1
            """, (row["mint"], row["pair"], row["stamp"], lower)).fetchone()
            row["prior_liq_pct"] = (
                100.0 * (row["liq"] / prior["liquidity_usd"] - 1.0)
                if prior and prior["liquidity_usd"] and row["liq"] is not None
                else None
            )
            row["prior_lag_min"] = (
                (parse_time(row["stamp"]) - parse_time(prior["scanned_at"])).total_seconds() / 60
                if prior else None
            )
        return examples, len(rows)

def stats(name, data, base=None):
    if not data:
        print(f"{name}: n=0")
        return
    n = len(data)
    r = sorted(x["ret"] for x in data)
    crashes = sum(x["ret"] <= -30 for x in data)
    extreme = sum(x["ret"] <= -70 for x in data)
    winners = sum(x["ret"] >= 10 for x in data)
    out = (f"{name}: n={n}, crash30={crashes}/{n} ({100*crashes/n:.1f}%), "
           f"crash70={extreme}, mean={mean(r):+.2f}%, median={median(r):+.2f}%, "
           f"worst={r[0]:+.2f}%, winners10={winners}")
    if base is not None:
        missed = sum(x["ret"] >= 10 for x in base) - winners
        out += f", excluded={len(base)-n}, missed_winners10={missed}"
    print(out)

def report(rows, label):
    print(f"\n=== {label} ===")
    high = [r for r in rows if r["vol"] >= 3000]
    low = [r for r in rows if r["vol"] < 3000]
    stats("Baseline high volume >=3000", high)
    stats("Control volume <3000", low)
    with_prior = [r for r in high if r["prior_liq_pct"] is not None]
    print(f"Pre-signal liquidity history: {len(with_prior)}/{len(high)} high-volume tokens")
    stats("High volume with measurable prior", with_prior)
    stats("No prior available (UNKNOWN)", [r for r in high if r["prior_liq_pct"] is None])
    # Compare safeguards to the SAME coverage-controlled high-volume population.
    for label, check in [
        ("no pre-entry liq drop >=20%", lambda r: r["prior_liq_pct"] > -20),
        ("no pre-entry liq drop >=40%", lambda r: r["prior_liq_pct"] > -40),
        ("buys/sells >=1.5", lambda r: r["buys"] >= 1.5 * max(r["sells"], 1)),
        ("liquidity >=50000", lambda r: r["liq"] >= 50000),
        ("liq stable and buys/sells >=1.5", lambda r: r["prior_liq_pct"] > -20 and r["buys"] >= 1.5 * max(r["sells"], 1)),
    ]:
        pool = with_prior if ("liq" in label and "liquidity >=50000" not in label) else high
        kept = [r for r in pool if check(r)]
        stats(label, kept, pool)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=DEFAULT_DB)
    args = parser.parse_args()
    rows, eligible_snapshots = load(args.db)
    print("MODE: READ_ONLY_EXPLORATORY_CRASH_RESEARCH")
    print("No trading, database writes, execution quotes, or live alerts.")
    print(f"Eligible snapshots with future outcomes={eligible_snapshots}; first eligible token observations={len(rows)}")
    split = int(len(rows)*0.6)
    print("Chronological 60/40 split. Filters are exploratory and NOT independently validated.")
    report(rows[:split], "OLDER 60%")
    report(rows[split:], "NEWER 40%")
    print("\nLIMITATIONS: historical screening; outcome availability selection; repeated market periods;")
    print("multiple compared thresholds; large outliers; no Jupiter fills or costs;")
    print("a missing pre-signal snapshot is UNKNOWN, not a zero liquidity change.")

if __name__ == "__main__":
    main()
