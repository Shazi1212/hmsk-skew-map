"""Pure computation: no network, fully unit-testable.

Everything here implements the seven decisions from config.yaml.
Keep it deterministic. If a value can't be trusted, return None and a reason,
never a plausible-looking number.
"""
from __future__ import annotations

import math
from datetime import date, datetime


def norm_cdf(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def bs_delta(kind: str, spot: float, strike: float, t_years: float, r: float, iv: float) -> float:
    """Black-Scholes delta (no dividend yield). iv as a decimal, e.g. 0.35."""
    if t_years <= 0 or iv <= 0 or spot <= 0 or strike <= 0:
        return float("nan")
    d1 = (math.log(spot / strike) + (r + 0.5 * iv * iv) * t_years) / (iv * math.sqrt(t_years))
    return norm_cdf(d1) if kind == "call" else norm_cdf(d1) - 1.0


def bs_price(kind: str, spot: float, strike: float, t_years: float, r: float, iv: float) -> float:
    """Black-Scholes price, no dividend yield. Same q=0 assumption as bs_delta,
    so leg selection and IV inversion stay consistent with each other."""
    if t_years <= 0 or iv <= 0 or spot <= 0 or strike <= 0:
        return float("nan")
    srt = iv * math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (r + 0.5 * iv * iv) * t_years) / srt
    d2 = d1 - srt
    disc = math.exp(-r * t_years)
    if kind == "call":
        return spot * norm_cdf(d1) - strike * disc * norm_cdf(d2)
    return strike * disc * norm_cdf(-d2) - spot * norm_cdf(-d1)


def bs_vega(spot: float, strike: float, t_years: float, r: float, iv: float) -> float:
    """Price sensitivity to a 1.00 change in vol (so a vol point is vega/100)."""
    if t_years <= 0 or iv <= 0 or spot <= 0 or strike <= 0:
        return 0.0
    srt = iv * math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (r + 0.5 * iv * iv) * t_years) / srt
    pdf = math.exp(-0.5 * d1 * d1) / math.sqrt(2.0 * math.pi)
    return spot * pdf * math.sqrt(t_years)


def implied_vol(kind: str, price, spot: float, strike: float, t_years: float, r: float,
                lo: float = 0.005, hi: float = 5.0, vol_tol: float = 1e-7,
                min_vega: float = 0.5):
    """Invert bs_price by bisection. Returns the IV as a decimal, or None.

    None means the price pins down no volatility worth reporting, for one of
    three reasons: it sits outside the no-arbitrage band, the vol lies outside
    [lo, hi], or vega is so small that price carries no information about vol.
    That last one bites on deep in-the-money strikes, where the price is almost
    all intrinsic and a wide range of vols produce the same number to the cent.
    Returning a confident figure there would be exactly the fabrication this
    whole change exists to stop, so it refuses instead.

    Bisection converges on the vol bracket rather than the price residual.
    Stopping on the residual looks tempting and is wrong: on a low-vega strike
    the residual goes tiny while the vol is still far from the truth.
    """
    if price is None or t_years <= 0 or spot <= 0 or strike <= 0:
        return None
    price = float(price)
    if price <= 0:
        return None

    disc = math.exp(-r * t_years)
    intrinsic = max(0.0, spot - strike * disc) if kind == "call" else max(0.0, strike * disc - spot)
    ceiling = spot if kind == "call" else strike * disc
    if price <= intrinsic or price >= ceiling:
        return None

    f_lo = bs_price(kind, spot, strike, t_years, r, lo) - price
    f_hi = bs_price(kind, spot, strike, t_years, r, hi) - price
    if not (math.isfinite(f_lo) and math.isfinite(f_hi)) or f_lo > 0 or f_hi < 0:
        return None

    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if (hi - lo) < vol_tol:
            break
        f = bs_price(kind, spot, strike, t_years, r, mid) - price
        if not math.isfinite(f):
            return None
        if f < 0:
            lo = mid
        else:
            hi = mid
    iv = 0.5 * (lo + hi)
    if bs_vega(spot, strike, t_years, r, iv) < min_vega:
        return None
    return iv


def mid_price(row):
    """The bid/ask midpoint, or None when there is no two-sided quote."""
    bid, ask = row.get("bid"), row.get("ask")
    if bid is None or ask is None:
        return None
    b, a = float(bid), float(ask)
    if b <= 0 or a <= 0:
        return None
    return (a + b) / 2.0


def implied_forward(calls, puts, t_years: float, r: float, spot_hint: float = None,
                    band: float = 0.15, min_pairs: int = 3):
    """The forward the chain itself implies, via put-call parity:
    F = K + e^{rT}(C - P), taken as the median over near-the-money strikes.

    Prefer this to any separately fetched spot. It cannot be stale relative to
    the quotes it is derived from, and it embeds the dividend that a q=0 model
    would otherwise ignore, which is the bias that would otherwise show up as
    fake put skew on exactly the dividend payers.

    Returns None when too few strikes carry a two-sided quote on both sides.
    """
    if t_years <= 0:
        return None
    cm = {float(c["strike"]): mid_price(c) for c in calls if c.get("strike") is not None}
    pm = {float(p["strike"]): mid_price(p) for p in puts if p.get("strike") is not None}
    grow = math.exp(r * t_years)
    fwds = []
    for k in sorted(set(cm) & set(pm)):
        c, p = cm[k], pm[k]
        if c is None or p is None:
            continue
        if spot_hint and not (spot_hint * (1 - band) <= k <= spot_hint * (1 + band)):
            continue
        fwds.append(k + grow * (c - p))
    if len(fwds) < min_pairs:
        return None
    fwds.sort()
    n = len(fwds)
    return fwds[n // 2] if n % 2 else 0.5 * (fwds[n // 2 - 1] + fwds[n // 2])


def pricing_spot(calls, puts, spot: float, t_years: float, r: float):
    """The spot to do option math against, and how far it sits from the feed's.

    Everything here prices with no dividend yield, so a forward of S*e^{rT} is
    assumed. Feeding back F*e^{-rT} makes that assumption true by construction,
    which is what keeps put and call IVs on the same footing.

    Returns (spot_to_use, drift_fraction_or_None).
    """
    fwd = implied_forward(calls, puts, t_years, r, spot_hint=spot)
    if fwd is None or fwd <= 0:
        return spot, None
    eff = fwd * math.exp(-r * t_years)
    drift = (eff - spot) / spot if spot else None
    return eff, drift


def apply_mid_iv(rows, kind: str, spot: float, t_years: float, r: float):
    """Re-derive every IV from the bid/ask midpoint.

    A provider's IV field is usually struck off the last trade, which on a
    quiet strike can be days old even where a live market exists. The mid is
    what someone would actually deal at right now.

    Returns (rows, stats). Rows are new dicts; a row whose mid yields no IV
    keeps iv=None and drops out of every downstream gate rather than falling
    back to a number nobody can defend.
    """
    out, solved, kept = [], 0, 0
    for c in rows:
        row = dict(c)
        iv = implied_vol(kind, mid_price(c), spot, c.get("strike") or 0.0, t_years, r)
        if iv is None:
            row["iv"] = None
            kept += 1
        else:
            row["iv"] = iv
            row["iv_source"] = "mid"
            solved += 1
        # A provider delta was computed against its own IV, so it no longer
        # matches ours. Drop it and let bs_delta work off the mid-implied vol.
        row.pop("delta", None)
        out.append(row)
    return out, {"solved": solved, "unpriced": kept}


def is_monthly(d: date) -> bool:
    """Standard US monthly expiration: third Friday."""
    return d.weekday() == 4 and 15 <= d.day <= 21


def pick_expiry(expiries, asof: date, min_dte: int, max_dte: int, prefer_monthly: bool = True):
    """Decision 2: one rule, every name, every run.

    Returns (expiry_iso, dte, note) or (None, None, reason).
    """
    cands = []
    for e in expiries:
        try:
            d = datetime.strptime(str(e), "%Y-%m-%d").date()
        except ValueError:
            continue
        dte = (d - asof).days
        if min_dte <= dte <= max_dte:
            cands.append((d, dte))
    if not cands:
        return None, None, f"no expiry inside the {min_dte}-{max_dte} DTE window"
    mid = (min_dte + max_dte) / 2.0
    monthlies = [c for c in cands if is_monthly(c[0])]
    pool = monthlies if (prefer_monthly and monthlies) else cands
    d, dte = min(pool, key=lambda c: abs(c[1] - mid))
    note = None
    if prefer_monthly and not monthlies:
        note = "no standard monthly in window; using nearest listed expiry"
    return d.isoformat(), dte, note


def quote_ok(row, require_bid: bool = True, max_spread_pct: float = 0.35):
    """Decision 5: is there a real two-sided market on this contract?

    An option with no bid has no price, so any IV quoted against it is fiction.
    A spread wider than max_spread_pct of the mid means the true price is
    uncertain by half that, which is far too much to read a skew from.

    Providers that ship no quotes at all cannot be judged here, so a row with
    neither bid nor ask passes and the older gates still apply. Returns
    (ok, reason); reason is None when ok.
    """
    bid, ask = row.get("bid"), row.get("ask")
    if bid is None and ask is None:
        return True, None
    b = float(bid) if bid is not None else 0.0
    a = float(ask) if ask is not None else 0.0
    if require_bid and b <= 0:
        return False, "no bid"
    if a <= 0:
        return False, "no ask"
    mid = (a + b) / 2.0
    if mid <= 0:
        return False, "no mid"
    if max_spread_pct and (a - b) / mid > max_spread_pct:
        return False, f"spread {(a - b) / mid:.0%} of mid"
    return True, None


def _quote_gate(q):
    """Pull the two-sided-market settings out of the quality config."""
    q = q or {}
    return bool(q.get("require_bid", True)), float(q.get("max_leg_spread_pct", 0.35))


def usable_iv(row) -> bool:
    iv = row.get("iv")
    return iv is not None and 0.005 < iv < 5.0


def select_leg(rows, kind: str, spot: float, t_years: float, r: float,
               target_abs_delta: float, q=None):
    """Decision 1: pick the contract whose delta is nearest the target.

    rows: iterable of dicts with strike, iv (decimal), oi, optionally bid/ask,
    and optionally a provider-supplied delta. Returns a dict or None.

    Contracts without a real two-sided market are skipped rather than rejecting
    the name, so this walks on to the next-best strike. That matters: the
    nearest-delta contract is often the least traded one on the board.
    """
    require_bid, max_spread = _quote_gate(q)
    target = target_abs_delta if kind == "call" else -target_abs_delta
    best = None
    for c in rows:
        iv = c.get("iv")
        k = c.get("strike")
        if k is None or not usable_iv(c):
            continue
        if q is not None and not quote_ok(c, require_bid, max_spread)[0]:
            continue
        d = c.get("delta")
        if d is None or not math.isfinite(d):
            d = bs_delta(kind, spot, k, t_years, r, iv)
        if not math.isfinite(d):
            continue
        score = abs(d - target)
        if best is None or score < best["gap"]:
            bid, ask = c.get("bid"), c.get("ask")
            mid = ((float(bid) + float(ask)) / 2.0) if (bid and ask) else None
            best = {
                "iv": iv * 100.0,
                "strike": float(k),
                "delta": round(d, 4),
                "gap": score,
                "oi": int(c.get("oi") or 0),
                "spread_pct": round((float(ask) - float(bid)) / mid, 4) if mid else None,
            }
    return best


def atm_iv(calls, puts, spot: float):
    """ATM IV as the mean of the nearest-the-money call and put IVs, in vol points."""
    def nearest(rows):
        valid = [c for c in rows if c.get("iv") and 0.005 < c["iv"] < 5.0 and c.get("strike")]
        if not valid:
            return None
        return min(valid, key=lambda c: abs(c["strike"] - spot))

    picks = [p for p in (nearest(calls), nearest(puts)) if p]
    if not picks:
        return None
    return sum(p["iv"] for p in picks) / len(picks) * 100.0


def skew_pts(atm: float | None, put_iv: float | None, call_iv: float | None):
    """Returns (normalized_skew, vol_points) or (None, None). All inputs in vol points."""
    if atm is None or put_iv is None or call_iv is None or atm <= 0:
        return None, None
    pts = put_iv - call_iv
    return pts / atm, pts


# US regular session, 09:30 to 16:00 local exchange time.
SESSION_OPEN_MIN = 9 * 60 + 30
SESSION_MINUTES = 390


def session_progress(now_local, session_date):
    """Fraction of the regular session elapsed, or None when the bar is done.

    None means the volume for that bar is final: either it belongs to an
    earlier session, or the close has passed. Pass a timezone-aware local
    exchange time; this stays pure so the caller owns the clock.
    """
    if now_local is None or session_date is None:
        return None
    if now_local.date() != session_date:
        return None
    mins = now_local.hour * 60 + now_local.minute - SESSION_OPEN_MIN
    if mins >= SESSION_MINUTES:
        return None
    return max(0.0, mins / SESSION_MINUTES)


def volume_fraction(progress: float) -> float:
    """Share of a normal session's volume done by this point of the day.

    Volume is U-shaped, not uniform: the first and last half hour each carry
    roughly an eighth of the day. Assuming a straight line would claim only
    7.7% of volume has traded half an hour in, when about 13% really has, and
    scaling by that would overstate RVOL by most of a half at the open, which
    is squarely inside the heavy/light thresholds. The curve below is the
    simplest shape that honours those two anchor points: a quadratic rate
    a + b(u - 0.5)^2 integrated over the session, with the constants fixed by
    the first half hour being 13% and the whole day being 100%.
    """
    u = min(max(float(progress), 0.0), 1.0)
    a, b = 0.558, 5.304
    return a * u + b * ((u - 0.5) ** 3 + 0.125) / 3.0


def relative_volume(today_volume, base_volumes, progress=None, min_progress: float = 0.15):
    """Today's volume against the recent average. Returns (rvol, provisional).

    While the session is still running only part of the day has traded, so the
    raw ratio reads light for no reason but the clock. Scaling by the share a
    normal day would have done by now removes that, and the result is flagged
    provisional because it is a projection, not a count.

    Very early on, that scaling divides by a small number and turns a quiet
    first ten minutes into a dramatic reading. Below min_progress it returns
    None: an unknown RVOL prints as "unknown volume" and costs one clause,
    where a fabricated one would change the verdict.
    """
    base = [float(v) for v in base_volumes if v is not None and v == v]
    avg = (sum(base) / len(base)) if base else 0.0
    if avg <= 0 or today_volume is None or today_volume != today_volume:
        return None, False
    if progress is None:
        return float(today_volume) / avg, False
    if progress < min_progress:
        return None, True
    share = volume_fraction(progress)
    if share <= 0:
        return None, True
    return (float(today_volume) / share) / avg, True


def quadrant(skew_norm, ret, ceiling: float):
    """Mirror of the dashboard's quadOf() at the default zero crosshair.

    The dashboard stays the source of truth for what the reader sees; this
    server-side copy exists only so the pipeline can alert on quadrant flips.
    Keep the two in sync when editing either one.
    Returns 'HEDGE', 'CHASE', 'FEAR', 'CONTRA', or None when unclassifiable.
    """
    if skew_norm is None or ret is None:
        return None
    try:
        s, r = float(skew_norm), float(ret)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(s) and math.isfinite(r)) or abs(s) > ceiling:
        return None
    if r >= 0:
        return "HEDGE" if s >= 0 else "CHASE"
    return "FEAR" if s >= 0 else "CONTRA"


def chain_stats(calls, puts, q=None):
    """How much of the chain is quoted, and how much of that is actually
    tradeable. Reported for every name, never a pass/fail on its own: dropping
    dead wings is healthy, and only the strike count in quality_check decides
    whether what is left is enough to read."""
    require_bid, max_spread = _quote_gate(q)
    out = {}
    for side, rows in (("put", puts), ("call", calls)):
        quoted = [c for c in rows if usable_iv(c)]
        live = [c for c in quoted if quote_ok(c, require_bid, max_spread)[0]]
        out[f"{side}_quoted"] = len(quoted)
        out[f"{side}_tradeable"] = len(live)
    return out


def quality_check(calls, puts, put_leg, call_leg, q):
    """Decision 5. Returns a list of reasons; empty list means chain_ok."""
    reasons = []
    require_bid, max_spread = _quote_gate(q)
    tradeable = lambda rows: [c for c in rows
                              if usable_iv(c) and quote_ok(c, require_bid, max_spread)[0]]

    min_strikes = int(q.get("min_strikes_per_side", 8))
    ok_c, ok_p = tradeable(calls), tradeable(puts)
    valid_c, valid_p = len(ok_c), len(ok_p)
    if valid_c < min_strikes or valid_p < min_strikes:
        reasons.append(f"only {valid_p}p/{valid_c}c strikes with a usable IV and a real "
                       f"market (need {min_strikes})")


    max_gap = float(q.get("max_delta_gap", 0.10))
    for name, leg in (("put", put_leg), ("call", call_leg)):
        if leg is None:
            reasons.append(f"no usable {name} leg")
        elif leg["gap"] > max_gap:
            reasons.append(f"nearest {name} strike lands at delta {leg['delta']:+.2f} (gap {leg['gap']:.2f})")

    min_oi = int(q.get("min_leg_open_interest", 25))
    ignore_missing = bool(q.get("ignore_oi_if_missing", True))
    have_oi = any((c.get("oi") or 0) > 0 for c in list(calls) + list(puts))
    if have_oi or not ignore_missing:
        for name, leg in (("put", put_leg), ("call", call_leg)):
            if leg is not None and leg["oi"] < min_oi:
                reasons.append(f"{name} leg open interest {leg['oi']} below {min_oi}")
    return reasons
