from datetime import date
from skewmap.compute import bs_delta, pick_expiry, select_leg, atm_iv, skew_pts, is_monthly

def test_bs_delta_sane():
    c = bs_delta("call", 100, 100, 45/365, 0.045, 0.35)
    p = bs_delta("put", 100, 100, 45/365, 0.045, 0.35)
    assert 0.45 < c < 0.60
    assert -0.55 < p < -0.40
    assert abs((c - p) - 1.0) < 1e-9  # put-call delta parity, q=0

def test_pick_expiry_prefers_monthly():
    asof = date(2026, 8, 28)
    exps = ["2026-09-04", "2026-09-18", "2026-09-25", "2026-10-16", "2026-11-20"]
    e, dte, note = pick_expiry(exps, asof, 30, 60, True)
    assert e == "2026-10-16" and is_monthly(date(2026, 10, 16)) and note is None
    e2, _, _ = pick_expiry(["2026-07-01"], asof, 30, 60, True)
    assert e2 is None

def test_select_leg_hits_target():
    spot, t, r = 100.0, 45/365, 0.045
    grid = [{"strike": k, "iv": 0.35, "oi": 100} for k in range(70, 131, 5)]
    put = select_leg(grid, "put", spot, t, r, 0.25)
    call = select_leg(grid, "call", spot, t, r, 0.25)
    assert put and call
    assert abs(put["delta"] + 0.25) < 0.08
    assert abs(call["delta"] - 0.25) < 0.08
    assert put["strike"] < spot < call["strike"]

def test_provider_delta_wins():
    rows = [{"strike": 90, "iv": 0.4, "oi": 1, "delta": -0.251},
            {"strike": 95, "iv": 0.4, "oi": 1, "delta": -0.35}]
    leg = select_leg(rows, "put", 100, 45/365, 0.045, 0.25)
    assert leg["strike"] == 90

def test_skew_math():
    sk, pts = skew_pts(40.0, 44.0, 38.0)
    assert abs(pts - 6.0) < 1e-9 and abs(sk - 0.15) < 1e-9
    assert skew_pts(None, 44.0, 38.0) == (None, None)

def test_atm_iv_nearest():
    calls = [{"strike": 95, "iv": 0.30}, {"strike": 100, "iv": 0.32}, {"strike": 105, "iv": 0.34}]
    puts = [{"strike": 95, "iv": 0.36}, {"strike": 100, "iv": 0.34}]
    assert abs(atm_iv(calls, puts, 101.0) - 33.0) < 1e-9
