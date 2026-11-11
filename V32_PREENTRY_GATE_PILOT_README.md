# V3.2 pre-entry Jupiter roundtrip gate — isolated pilot

This branch adds an **opt-in**, fail-closed **quote-only** gate to the
existing `m1_v3_2_forward_quotes.py`. No live orders, wallet, signing,
production service changes, database migrations or retrospective fills.

## What changes when `--preentry-gate` is explicitly enabled

Before registering a hypothetical entry:

1. Reuse the original 25-USDC Jupiter buy quote.
2. Request the reverse Jupiter sell quote for **exactly** the bought raw quantity.
3. Refuse entry if buy/sell quotes are absent, invalid, or roundtrip return is
   worse than **-3%**, using the same pilot threshold as B2.
4. Store decision and (on a pass) the immediate sell quote as the first
   observed quote. The next quote refresh still uses the established
   60-second pacing.
5. No history is overwritten; previously registered positions do not
   retroactively pass or fail a newly introduced gate.

The quote request cap stays at **4 per run**; a gated entry reserves two
quote slots. Default behavior is unchanged unless the flag is supplied.

This **does not yet** integrate token contract authority, holder concentration,
LP lock, Token-2022 extensions, or real settlement checks. A Jupiter quote
does **not** prove actual sellability or guaranteed execution. This is a
limited initial integration of the existing B2-style roundtrip gate, NOT a
complete safety approval.

## Test safely in a separate Git worktree on the VPS

From `~/gta6-v63-test`:

```bash
git fetch origin feature/v32-preentry-gate-pilot
git worktree add ~/gta6-v32-gate-pilot origin/feature/v32-preentry-gate-pilot
cd ~/gta6-v32-gate-pilot
~/gta6-source/.venv/bin/python -m unittest discover -s tests -p 'test_v32_preentry_gate.py' -v
~/gta6-source/.venv/bin/python -m unittest discover -s tests -p 'test_m1_v3_2_forward_quotes.py' -v
```

The tests use fake quote functions and **make no Jupiter API calls**.
**Do not** point the pilot at `~/gta6-v63-test/m1_v3_2_state.json`.
**Do not** change existing systemd service files or timers.

A separate future prospective shadow pilot would need a dedicated state
path and its own reviewed timer, then compare blocked vs. approved cohorts
without retrospectively pretending historical quotes existed.
