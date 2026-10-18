# Skew Map

A daily options-positioning board. A small Python pipeline pulls option
chains (free, via `yfinance`), computes 25-delta put/call skew for every name
on one fixed rule set, and feeds a single-file dashboard (`index.html`) that
sorts each name into four quadrants (Contrarian bid, Chase, Hedged rally,
Fear) and writes the same one-sentence read per name, every session.

It is a where-to-look layer: it shows what people are paying to be positioned,
not what to do about it.

Tool page and demo snapshot: https://hmsk.life/tools/skew-map/

> **Disclaimer.** Educational tool only. Not investment advice. Nothing here
> is a recommendation to buy, sell or hold any security or option. The data
> comes from free, delayed, unofficial sources (Yahoo Finance via `yfinance`)
> and may be delayed, incomplete or wrong. Check every number yourself and
> make your own decisions at your own risk. Keep the disclaimer footer in
> `index.html` if you put this board in front of anyone.

## Run it

Python 3 (tested with 3.12).

    python3 -m venv .venv && . .venv/bin/activate
    pip install -r requirements.txt
    python -m skewmap.fetch --config config.yaml   # real data via yfinance, about 30 s
    python3 -m http.server 8000                    # open http://localhost:8000

No market data available (offline, CI)? `--demo` writes a synthetic board.
Quick spot check on a few names: `--tickers NVDA,MU`. Opened without a
`data/` folder the dashboard falls back to a manual workbench where you type
in the IV readings yourself.

Tests (run them whenever you touch `skewmap/compute.py`):

    pip install pytest && python -m pytest tests -q

## What you get

    data/latest.json     the board: raw legs per name, failures and warnings by name
    data/history.json    per-ticker series; powers the weekly change column and
                         quadrant-flip alerts
    data/archive/        dated copy of every build

Names the pipeline could not cover, chains too thin to trust, and readings
past the sanity ceiling are listed by name on the board, never silently
dropped.

## Configure

Everything lives in `config.yaml`, nothing in code: the delta level for the
two strikes, the expiry window, the benchmark and minimum sector size, the
chain quality bar, the sanity ceiling, history retention, sentence thresholds,
and the universe of tickers and sectors.

## Schedule daily runs

**GitHub Actions.** `.github/workflows/update.yml` runs the tests and builds
the board every weekday at 21:20 UTC (after the US close), then commits
`data/`. Enable Actions on your copy of the repo and serve the page with
GitHub Pages (deploy from the main branch, root) or any static host. Two
optional repository secrets, referenced by name only:

* `ALERT_WEBHOOK_URL`: Slack-style incoming webhook for quadrant flips and
  data warnings.
* `DATABASE_URL`: Postgres connection string for the optional mirror
  (`skewmap/store.py`, read back by the optional `api/` serverless functions).

**cron.** On any machine with the venv above:

    20 21 * * 1-5  cd /path/to/skew-map && .venv/bin/python -m skewmap.fetch --config config.yaml >> fetch.log 2>&1

and point any static web server at the folder.

## Docker (untested)

`docker-compose.yml` builds one container that serves the dashboard on port
8080 and rebuilds the board every weekday at `RUN_AT_UTC`. It was written
without Docker available and has not been run yet.

    docker compose up -d     # then open http://localhost:8080

## Data sources and licensing

* `yfinance` (default): free, no key, delayed, unofficial Yahoo endpoints.
  Fine for personal and educational use. Yahoo's data is not licensed for
  redistribution, so check the terms before publishing boards built from it.
* `tradier` (optional): set `provider: tradier` in `config.yaml` and a
  `TRADIER_TOKEN` environment variable with your own token. The adapter is
  written to Tradier's published API docs and has never been run against a
  real token; verify it with `TRADIER_ENV=sandbox` first.
* Anything else: implement the small interface at the top of
  `skewmap/providers.py`.

## License

MIT, see `LICENSE`. Copyright (c) 2026 Hafiz Muhammad Sharoz Khalid.
