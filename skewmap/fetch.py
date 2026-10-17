"""Build the board: python -m skewmap.fetch --config config.yaml

Outputs
  data/latest.json    what the dashboard renders
  data/history.json   per-ticker {date, skew, pts} series; feeds the Delta column
  data/archive/board-<session>.json   immutable daily copy

House rules (do not relax these):
  * Every name that could not be covered lands in failures[] with a reason.
    Nothing is dropped silently; the dashboard prints failures by name.
  * The pipeline ships raw legs (atm/put/call IV, return, rvol). Verdicts,
    quadrants and the sanity-ceiling rejection are computed client-side from
    config-mirrored thresholds, so a threshold change never needs a refetch.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .compute import (pick_expiry, select_leg, atm_iv, skew_pts, quality_check,
                      quadrant, chain_stats, apply_mid_iv, pricing_spot)
from .providers import make_provider, DemoProvider, ProviderError


def load_config(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def build_board(cfg: dict, only=None, demo: bool = False) -> dict:
    dec = cfg["decisions"]
    sen = cfg.get("sentence", {})
    provider = DemoProvider(cfg) if demo else make_provider(cfg)
    overrides = cfg.get("earnings_overrides") or {}
    now = datetime.now(timezone.utc)

    rows, failures = [], []
    universe = cfg.get("universe") or []
    if only:
        keep = {t.strip().upper() for t in only}
        universe = [u for u in universe if u["t"].upper() in keep]

    if isinstance(provider, DemoProvider):
        spy_1m = provider.spy_1m()
        session = now.date().isoformat()
        demo_rows = {r["t"]: r for r in provider.rows()}
        for u in universe:
            r = demo_rows.get(u["t"])
            if not r:
                failures.append({"t": u["t"], "reason": "not in demo set"})
                continue
            r = dict(r)
            r["sector"] = u.get("sector", "")
            r["watch"] = bool(u.get("watch"))
            if u["t"] in overrides:
                r["earn"] = overrides[u["t"]]
            rows.append(r)
    else:
        # Benchmark return first (decision 4).
        bench = dec.get("benchmark", "SPY")
        try:
            bctx = provider.price_context(bench)
            spy_1m, session = round(bctx["ret1m"], 2), bctx["session"]
        except Exception as e:  # noqa: BLE001
            spy_1m, session = None, now.date().isoformat()
            failures.append({"t": bench, "reason": f"benchmark fetch failed: {e}"})

        r_rate = float(cfg.get("risk_free_rate", 0.045))
        delta_lvl = float(dec.get("delta_level", 25)) / 100.0
        exp = dec.get("expiry", {})

        for u in universe:
            t = u["t"]
            try:
                ctx = provider.price_context(t)
                expiries = provider.expiries(ctx["handle"])
                asof = datetime.strptime(ctx["session"], "%Y-%m-%d").date()
                expiry, dte, note = pick_expiry(
                    expiries, asof,
                    int(exp.get("min_dte", 30)), int(exp.get("max_dte", 60)),
                    bool(exp.get("prefer_monthly", True)),
                )
                if expiry is None:
                    failures.append({"t": t, "reason": note})
                    continue
                calls, puts = provider.chain(ctx["handle"], expiry)
                t_years = dte / 365.0
                qual = dec.get("quality", {})

                # Price against the forward the chain implies, not the last
                # close. The close can be a session stale relative to the
                # quotes, and it ignores dividends; either one tilts put and
                # call IVs in opposite directions and manufactures skew.
                spot, drift = pricing_spot(calls, puts, ctx["spot"], t_years, r_rate)

                iv_stats = {}
                if ctx.get("spot_source") and ctx["spot_source"] != "daily close":
                    iv_stats["spot_source"] = ctx["spot_source"]
                if ctx.get("session_progress") is not None:
                    iv_stats["session_progress"] = ctx["session_progress"]
                if drift is not None:
                    iv_stats["spot_feed"] = round(ctx["spot"], 4)
                    iv_stats["spot_priced"] = round(spot, 4)
                    iv_stats["spot_drift"] = round(drift, 4)
                if qual.get("iv_from_mid", True):
                    calls, cs = apply_mid_iv(calls, "call", spot, t_years, r_rate)
                    puts, ps = apply_mid_iv(puts, "put", spot, t_years, r_rate)
                    iv_stats.update({"iv_from_mid": True,
                                     "iv_solved": ps["solved"] + cs["solved"],
                                     "iv_unpriced": ps["unpriced"] + cs["unpriced"]})
                put_leg = select_leg(puts, "put", spot, t_years, r_rate, delta_lvl, qual)
                call_leg = select_leg(calls, "call", spot, t_years, r_rate, delta_lvl, qual)
                a_iv = atm_iv(calls, puts, spot)
                stats = chain_stats(calls, puts, qual)
                if a_iv is None or put_leg is None or call_leg is None:
                    failures.append({"t": t, "reason": "no strike with a usable IV and a "
                                                       f"two-sided market ({stats['put_tradeable']}p/"
                                                       f"{stats['call_tradeable']}c of "
                                                       f"{stats['put_quoted']}p/{stats['call_quoted']}c quoted)"})
                    continue
                reasons = quality_check(calls, puts, put_leg, call_leg, qual)
                if note:
                    reasons.append(note)
                earn = overrides.get(t) or provider.next_earnings(ctx["handle"])
                rows.append({
                    "t": t,
                    "sector": u.get("sector", ""),
                    "atm": round(a_iv, 2),
                    "put": round(put_leg["iv"], 2),
                    "call": round(call_leg["iv"], 2),
                    "ret": round(ctx["ret1m"], 2),
                    "rvol": round(ctx["rvol"], 2) if ctx["rvol"] is not None else None,
                    # Mid-session RVOL is a projection to a full day, not a count.
                    "rvol_partial": bool(ctx.get("rvol_provisional")),
                    "earn": earn,
                    "chain": len(reasons) == 0,
                    "watch": bool(u.get("watch")),
                    "meta": {
                        "expiry": expiry, "dte": dte,
                        "put_strike": put_leg["strike"], "put_delta": put_leg["delta"],
                        "call_strike": call_leg["strike"], "call_delta": call_leg["delta"],
                        "oi_put": put_leg["oi"], "oi_call": call_leg["oi"],
                        "spread_put": put_leg.get("spread_pct"),
                        "spread_call": call_leg.get("spread_pct"),
                        **stats,
                        **(iv_stats or {}),
                        "quality_notes": reasons or None,
                    },
                })
            except (ProviderError, Exception) as e:  # noqa: BLE001
                failures.append({"t": t, "reason": str(e) or e.__class__.__name__})

    rows.sort(key=lambda r: (r.get("sector", ""), r["t"]))
    return {
        "as_of": now.isoformat(timespec="seconds"),
        "session_date": session,
        "provider": provider.name,
        "spy_1m": spy_1m,
        "expiry_rule": f"monthly {dec['expiry'].get('min_dte', 30)}-{dec['expiry'].get('max_dte', 60)} DTE",
        "decisions": {
            "delta_level": dec.get("delta_level", 25),
            "sanity_ceiling": dec.get("sanity_ceiling", 0.8),
            "min_sector_names": dec.get("min_sector_names", 3),
            "agreement_min_pct": dec.get("agreement_min_pct", 65),
            "event_window_days": sen.get("event_window_days", 35),
            # Decision 5 in full, so the dashboard can state the bar the build
            # actually applied instead of describing a manual toggle.
            "quality": dict(dec.get("quality", {})),
            "benchmark": dec.get("benchmark", "SPY"),
        },
        "rows": rows,
        "failures": failures,
    }


def update_history(board: dict, path: Path, keep_days: int) -> dict:
    hist = {}
    if path.exists():
        try:
            hist = json.loads(path.read_text())
        except json.JSONDecodeError:
            hist = {}
    dates = set(hist.get("_dates") or [])
    sess = board["session_date"]
    dates.add(sess)
    dates = sorted(dates)[-keep_days:]
    for r in board["rows"]:
        sk, pts = skew_pts(r["atm"], r["put"], r["call"])
        if sk is None:
            continue
        series = [e for e in hist.get(r["t"], []) if e.get("date") != sess and e.get("date") in dates]
        entry = {"date": sess, "skew": round(sk, 4), "pts": round(pts, 2), "ok": bool(r.get("chain"))}
        if r.get("ret") is not None:
            entry["ret"] = r["ret"]
        series.append(entry)
        hist[r["t"]] = sorted(series, key=lambda e: e["date"])
    hist["_dates"] = dates
    path.write_text(json.dumps(hist, indent=1))
    return hist


def _cur_and_prev(series, sess):
    cur = next((e for e in series if e.get("date") == sess), None)
    prev = [e for e in series if e.get("date", "") < sess]
    return cur, (prev[-1] if prev else None)


def diff_guard(board: dict, hist: dict, max_move: float):
    """Backlog item 3: a one-session skew jump past max_move is usually a data
    problem (one bad mark on one leg), not a market move. Warn loudly; the name
    still ships, flagged in warnings[], never dropped."""
    warnings = []
    for r in board["rows"]:
        cur, last = _cur_and_prev(hist.get(r["t"]) or [], board["session_date"])
        if cur is None or last is None:
            continue
        try:
            move = float(cur["skew"]) - float(last["skew"])
        except (KeyError, TypeError, ValueError):
            continue
        if abs(move) > max_move:
            warnings.append({
                "t": r["t"],
                "reason": (f"skew jumped {move:+.3f} in one session"
                           f" ({last['date']} {float(last['skew']):+.3f} to {float(cur['skew']):+.3f});"
                           f" usually a data problem, re-pull the chain before believing it"),
            })
    return warnings


def quadrant_flips(board: dict, hist: dict, ceiling: float):
    """Backlog item 4: quadrant yesterday vs today per name, zero crosshair.
    Entries written before the ret/ok fields existed cannot be classified and
    simply produce no flip; nothing is guessed."""
    flips = []
    for r in board["rows"]:
        cur, last = _cur_and_prev(hist.get(r["t"]) or [], board["session_date"])
        if cur is None or last is None:
            continue
        q_now = quadrant(cur.get("skew"), cur.get("ret"), ceiling) if cur.get("ok") else None
        q_then = quadrant(last.get("skew"), last.get("ret"), ceiling) if last.get("ok") else None
        if q_now and q_then and q_now != q_then:
            flips.append({"t": r["t"], "from": q_then, "to": q_now,
                          "from_date": last["date"], "date": cur["date"]})
    return flips


QUAD_NAMES = {"CONTRA": "Contrarian bid", "CHASE": "Chase", "HEDGE": "Hedged rally", "FEAR": "Fear"}


def report_alerts(board: dict, flips: list) -> None:
    """Print warnings and flips loudly; POST them to ALERT_WEBHOOK_URL if set
    (Slack incoming-webhook shape, {"text": ...}). A webhook failure warns and
    never breaks the build."""
    warnings = board.get("warnings") or []
    for w in warnings:
        print(f"WARNING {w['t']}: {w['reason']}", file=sys.stderr)
    for f in flips:
        print(f"FLIP {f['t']}: {QUAD_NAMES[f['from']]} -> {QUAD_NAMES[f['to']]} ({f['from_date']} to {f['date']})")
    url = os.environ.get("ALERT_WEBHOOK_URL")
    if not url or not (flips or warnings):
        return
    lines = [f"skew board {board['session_date']}: {len(flips)} quadrant flip(s), {len(warnings)} data warning(s)"]
    lines += [f"{f['t']}: {QUAD_NAMES[f['from']]} -> {QUAD_NAMES[f['to']]}" for f in flips]
    lines += [f"WARN {w['t']}: {w['reason']}" for w in warnings]
    try:
        import requests
        requests.post(url, json={"text": "\n".join(lines)}, timeout=10).raise_for_status()
        print("alert webhook posted")
    except Exception as e:  # noqa: BLE001
        print(f"WARNING alert webhook failed: {e}", file=sys.stderr)


def summarize(board: dict, hist: dict) -> None:
    ceiling = board["decisions"]["sanity_ceiling"]
    print(f"\nboard {board['session_date']}  provider: {board['provider']}")
    print(f"benchmark 1M: {board['spy_1m']}%   names: {len(board['rows'])}   failures: {len(board['failures'])}\n")
    for r in board["rows"]:
        sk, pts = skew_pts(r["atm"], r["put"], r["call"])
        flag = ""
        if sk is not None and abs(sk) > ceiling:
            flag = "  << past sanity ceiling, dashboard will reject"
        elif not r["chain"]:
            flag = "  << thin (" + "; ".join(r["meta"].get("quality_notes") or []) + ")"
        series = hist.get(r["t"], [])
        prev = series[-2]["skew"] if len(series) >= 2 else None
        delta = f"  d1w {sk - prev:+.3f}" if (prev is not None and sk is not None) else ""
        print(f"  {r['t']:<5} {r.get('sector', ''):<11} skew {sk:+.3f}  pts {pts:+.1f}  1M {r['ret']:+6.1f}%{delta}{flag}")
    for f in board["failures"]:
        print(f"  {f['t']:<5} NOT COVERED: {f['reason']}")
    print()


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build the skew board")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--tickers", help="comma-separated subset, for spot checks")
    ap.add_argument("--demo", action="store_true", help="synthetic board, no network")
    ap.add_argument("--out", default="data")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    only = args.tickers.split(",") if args.tickers else None
    board = build_board(cfg, only=only, demo=args.demo)

    out = Path(args.out)
    (out / "archive").mkdir(parents=True, exist_ok=True)
    hist = update_history(board, out / "history.json",
                          int(cfg["decisions"].get("history_keep_days", 180)))
    guards = cfg.get("guards") or {}
    board["warnings"] = diff_guard(board, hist, float(guards.get("max_session_skew_move", 0.5)))
    flips = quadrant_flips(board, hist, float(cfg["decisions"].get("sanity_ceiling", 0.8)))
    (out / "latest.json").write_text(json.dumps(board, indent=1))
    (out / "archive" / f"board-{board['session_date']}.json").write_text(json.dumps(board, indent=1))
    summarize(board, hist)
    report_alerts(board, flips)

    dsn = os.environ.get("DATABASE_URL")
    if dsn:
        try:
            from .store import save_board
            n = save_board(board, dsn)
            print(f"postgres mirror: boards[{board['session_date']}] upserted with {n} name rows")
        except Exception as e:  # noqa: BLE001
            print(f"WARNING postgres mirror failed: {e}", file=sys.stderr)

    if not board["rows"]:
        print("ERROR: zero names covered; not a usable board", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
