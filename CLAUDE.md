# CLAUDE.md

## What this is

An options-skew positioning board. A Python pipeline pulls option chains,
computes put/call skew per a fixed rule set (the "seven decisions" in
config.yaml), and writes JSON that a single-file dashboard renders. The
dashboard classifies every name into one of four quadrants (Contrarian bid,
Chase, Hedged rally, Fear) and writes a fixed-template sentence per name.

This is a positioning layer, a where-to-look tool. It must never produce
entries, price targets, or trade recommendations. Keep the disclaimer footer
in index.html intact in every build.

## Layout

    config.yaml            the seven decisions + universe (edit here, not in code)
    skewmap/compute.py     pure math: BS delta, expiry pick, leg select, skew (unit-tested)
    skewmap/providers.py   yfinance (default, free), Tradier (optional, needs token), demo
    skewmap/fetch.py       CLI orchestrator; writes data/latest.json + history.json
    skewmap/store.py       optional Postgres mirror; on only when DATABASE_URL is set
    api/                   optional serverless read proxies for that mirror
    index.html             dual-mode dashboard: live (reads data/) or manual workbench
    data/                  pipeline output
    tests/                 pytest for the math core
    .github/workflows/update.yml   weekday cron after the US close

## Commands

    pip install -r requirements.txt pytest
    python -m pytest tests -q                       # 48 tests, must stay green
    python -m skewmap.fetch --config config.yaml --demo          # offline synthetic board
    python -m skewmap.fetch --config config.yaml --tickers NVDA,MU   # quick real spot check
    python -m skewmap.fetch --config config.yaml                 # full real build
    python3 -m http.server 8000                     # then open http://localhost:8000

## Invariants (do not relax)

1. Nothing is dropped silently. Every uncovered name goes to failures[] with a
   reason; the dashboard prints failures, thin chains, and rejections by name.
2. The pipeline ships raw legs (ATM/put/call IV, return, RVOL). Quadrants,
   sentences, and the sanity-ceiling rejection are computed client-side from
   thresholds mirrored in latest.json, so threshold changes never need a refetch.
3. Sector aggregates use raw vol points, never normalized skew. Normalized
   skew is for ranking names within a group and for the map. This asymmetry is
   deliberate (dividing by a small ATM manufactures fake fear in quiet sectors).
4. The sanity ceiling is set by plausibility for the asset class, never fitted
   to today's ugliest data point.
5. One expiry rule for every name, every run. If you change the rule, the
   history series breaks; say so in the commit message and consider archiving
   data/history.json first.
6. compute.py stays pure (no network) and unit-tested.
7. Never drop the session in progress. Yahoo serves it as a row with a real
   volume and a NaN close, and fast_info carries that session's actual price,
   so repair the row (providers.repaired_close) rather than filtering it out.
   Dropping it rolls the whole board back a session while the option chain
   stays current, which is how spot came to be 5% out. A repair further than
   50% from the previous close is refused: a missing session is a far cheaper
   failure than a wrong price, which moves spot, the return and every delta at
   once.
8. Option math prices against the forward the chain itself implies, never the
   feed's last close. Parity gives F = K + e^rT(C - P); the median over
   near-the-money strikes becomes the pricing spot via F*e^-rT. A close can be
   a whole session stale relative to the quotes and it ignores dividends, and
   either error pushes put and call IV in opposite directions, which is
   indistinguishable from real skew. The check that this is right is that ATM
   put IV and call IV agree: on 2026-08-30 COIN read 81.5 vs 34.0 off the feed
   close and 62.8 vs 62.8 off the forward. meta.spot_drift reports the gap.
9. Never compare a part-day volume against whole-day averages. Since the build
   runs mid-session, the last bar's volume is partial, and the raw ratio calls
   a heavy day light purely because of the clock. Scale by the share of a
   normal session's volume done by that time (volume_fraction, U-shaped: the
   first and last half hour are about an eighth of the day each, so a straight
   line is not good enough), flag the result provisional, and report nothing at
   all before min_session_progress_for_rvol, where the scaling amplifies noise
   more than it removes bias.
10. A leg needs a two-sided market. No bid means no price, so any IV quoted
   against it is fiction: on 2026-08-28 SO printed a skew of -1.48 off a call
   struck at 100 quoted 0.00/2.25 with a Yahoo IV of 47.9, and open interest
   of 36 cleared the old bar. select_leg skips such strikes and walks on to
   the next, so this costs coverage rather than inventing numbers. Expect
   fewer usable names, and read that as the board being honest.

## Style

Plain Python, no framework. Small functions, explicit reasons in strings.
Do not use em dashes anywhere in code comments, docs, or UI copy.
