"""Optional Postgres mirror of every build (any Postgres, e.g. Neon).

The dashboard never reads from here; it renders data/latest.json and that
invariant stands. This is the durable, queryable archive instead: one row
per session in boards (full board JSON included), one row per name per
session in board_rows, upserted so a rebuild of the same session replaces
itself. Set DATABASE_URL to turn it on; leave it unset and the pipeline
behaves exactly as before. A database failure warns and never breaks the
build.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .compute import skew_pts

SCHEMA = """
create table if not exists boards (
  session_date date primary key,
  as_of timestamptz not null,
  provider text not null,
  spy_1m double precision,
  board jsonb not null
);
create table if not exists board_rows (
  session_date date not null,
  ticker text not null,
  sector text,
  atm double precision,
  put_iv double precision,
  call_iv double precision,
  skew double precision,
  pts double precision,
  ret double precision,
  rvol double precision,
  earn date,
  chain_ok boolean not null,
  watch boolean not null,
  meta jsonb,
  primary key (session_date, ticker)
);
"""


def _date_or_none(v):
    try:
        datetime.strptime(str(v), "%Y-%m-%d")
        return str(v)
    except (TypeError, ValueError):
        return None


def rows_for_db(board: dict):
    """Pure shaping: board rows to board_rows tuples, skew precomputed."""
    out = []
    for r in board["rows"]:
        sk, pts = skew_pts(r.get("atm"), r.get("put"), r.get("call"))
        out.append((
            board["session_date"], r["t"], r.get("sector") or None,
            r.get("atm"), r.get("put"), r.get("call"),
            round(sk, 4) if sk is not None else None,
            round(pts, 2) if pts is not None else None,
            r.get("ret"), r.get("rvol"), _date_or_none(r.get("earn")),
            bool(r.get("chain")), bool(r.get("watch")),
            json.dumps(r["meta"]) if r.get("meta") is not None else None,
        ))
    return out


def save_board(board: dict, dsn: str) -> int:
    """Upsert one build into Postgres. Returns the number of name rows."""
    import psycopg2

    rows = rows_for_db(board)
    conn = psycopg2.connect(dsn)
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(SCHEMA)
                cur.execute(
                    """insert into boards (session_date, as_of, provider, spy_1m, board)
                       values (%s, %s, %s, %s, %s)
                       on conflict (session_date) do update set
                         as_of = excluded.as_of, provider = excluded.provider,
                         spy_1m = excluded.spy_1m, board = excluded.board""",
                    (board["session_date"], board["as_of"], board["provider"],
                     board.get("spy_1m"), json.dumps(board)),
                )
                cur.executemany(
                    """insert into board_rows (session_date, ticker, sector, atm,
                         put_iv, call_iv, skew, pts, ret, rvol, earn,
                         chain_ok, watch, meta)
                       values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       on conflict (session_date, ticker) do update set
                         sector = excluded.sector, atm = excluded.atm,
                         put_iv = excluded.put_iv, call_iv = excluded.call_iv,
                         skew = excluded.skew, pts = excluded.pts,
                         ret = excluded.ret, rvol = excluded.rvol,
                         earn = excluded.earn, chain_ok = excluded.chain_ok,
                         watch = excluded.watch, meta = excluded.meta""",
                    rows,
                )
    finally:
        conn.close()
    return len(rows)


def main(argv=None):
    """Rebuild the database from archived boards:

        python -m skewmap.store [--dir data/archive]

    Every archive is upserted, so this is safe to re-run and is the way to
    make the database a true superset of data/history.json. The dashboard
    picks whichever source carries more sessions, so a database that is
    behind the JSON is never a data loss, only a missed upgrade.
    """
    import argparse
    import os
    import sys

    ap = argparse.ArgumentParser(description="Backfill Postgres from archived boards")
    ap.add_argument("--dir", default="data/archive")
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL"))
    args = ap.parse_args(argv)

    if not args.dsn:
        print("DATABASE_URL is not set and --dsn was not given", file=sys.stderr)
        return 1
    files = sorted(Path(args.dir).glob("board-*.json"))
    if not files:
        print(f"no archived boards found in {args.dir}", file=sys.stderr)
        return 1

    total = 0
    for f in files:
        board = json.loads(f.read_text())
        n = save_board(board, args.dsn)
        total += n
        print(f"  {f.name}: session {board['session_date']}, {n} name rows")
    print(f"{len(files)} session(s), {total} name rows upserted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
