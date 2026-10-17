"""Step three: derive IV from the bid/ask mid instead of the feed's own field.

The feed's IV is struck off the last trade. On a strike that has not traded in
days, that number describes a market that no longer exists, while the quotes
describe the one that does.
"""
import math

from skewmap.compute import (bs_price, bs_vega, implied_vol, mid_price,
                             apply_mid_iv, bs_delta)

S, T, R = 100.0, 45 / 365, 0.045


def test_put_call_parity_holds():
    c = bs_price("call", S, 100.0, T, R, 0.35)
    p = bs_price("put", S, 100.0, T, R, 0.35)
    assert abs((c - p) - (S - 100.0 * math.exp(-R * T))) < 1e-9


def test_inversion_round_trips_wherever_vega_is_real():
    checked = 0
    for kind in ("call", "put"):
        for k in (80.0, 95.0, 100.0, 110.0, 125.0):
            for iv in (0.12, 0.35, 0.9):
                price = bs_price(kind, S, k, T, R, iv)
                back = implied_vol(kind, price, S, k, T, R)
                if bs_vega(S, k, T, R, iv) < 0.5:
                    assert back is None, (kind, k, iv, back)   # refused, not guessed
                    continue
                assert back is not None, (kind, k, iv)
                assert abs(back - iv) < 1e-4, (kind, k, iv, back)
                checked += 1
    assert checked >= 20, "the round trip must cover the strikes we actually read"


def test_a_flat_price_does_not_produce_a_confident_vol():
    """Deep in the money, price is nearly all intrinsic and vega collapses, so
    a wide band of vols gives the same price to the cent. Bisection will still
    converge on something; the point is that we refuse to report it."""
    k, true_iv = 80.0, 0.12
    assert bs_vega(S, k, T, R, true_iv) < 0.5
    price = bs_price("call", S, k, T, R, true_iv)
    assert implied_vol("call", price, S, k, T, R) is None
    # And the reason really is flatness: a very different vol prices the same.
    assert abs(bs_price("call", S, k, T, R, 0.05) - price) < 0.01


def test_no_volatility_reproduces_an_arbitrage_price():
    # Below intrinsic, and at or above the underlying: no IV exists, so say so
    # rather than returning a number that would look perfectly plausible.
    assert implied_vol("call", 0.01, S, 80.0, T, R) is None      # under intrinsic
    assert implied_vol("call", S + 1, S, 100.0, T, R) is None    # above the stock
    assert implied_vol("put", 0.0, S, 100.0, T, R) is None
    assert implied_vol("call", None, S, 100.0, T, R) is None


def test_vol_outside_the_search_band_is_refused():
    tiny = bs_price("call", S, 100.0, T, R, 0.001)
    huge = bs_price("call", S, 100.0, T, R, 8.0)
    assert implied_vol("call", tiny, S, 100.0, T, R) is None
    assert implied_vol("call", huge, S, 100.0, T, R) is None


def test_mid_price_needs_two_sides():
    assert mid_price({"bid": 1.0, "ask": 1.4}) == 1.2
    assert mid_price({"bid": 0.0, "ask": 2.25}) is None
    assert mid_price({"bid": 1.0}) is None
    assert mid_price({"strike": 90.0}) is None


def test_a_stale_last_trade_no_longer_sets_the_leg():
    """The failure this step exists to stop: quotes say one thing, the feed's
    IV field says another, and the field wins because nobody checked."""
    true_iv = 0.30
    price = bs_price("call", S, 110.0, T, R, true_iv)
    rows = [{"strike": 110.0, "iv": 0.95, "oi": 10,      # fiction from a stale print
             "bid": round(price - 0.02, 4), "ask": round(price + 0.02, 4)}]
    out, stats = apply_mid_iv(rows, "call", S, T, R)
    assert stats == {"solved": 1, "unpriced": 0}
    assert abs(out[0]["iv"] - true_iv) < 0.01
    assert out[0]["iv_source"] == "mid"


def test_unpriceable_rows_lose_their_iv_rather_than_falling_back():
    rows = [{"strike": 100.0, "iv": 0.479, "oi": 36, "bid": 0.0, "ask": 2.25}]
    out, stats = apply_mid_iv(rows, "call", S, T, R)
    assert out[0]["iv"] is None and stats["unpriced"] == 1


def test_a_provider_delta_is_dropped_so_it_cannot_contradict_the_new_iv():
    price = bs_price("put", S, 90.0, T, R, 0.28)
    rows = [{"strike": 90.0, "iv": 0.9, "oi": 5, "delta": -0.44,
             "bid": price - 0.01, "ask": price + 0.01}]
    out, _ = apply_mid_iv(rows, "put", S, T, R)
    assert "delta" not in out[0]
    # Delta now follows from the mid-implied vol, and disagrees with the old one.
    assert abs(bs_delta("put", S, 90.0, T, R, out[0]["iv"]) + 0.44) > 0.05


def test_forward_is_recovered_from_parity_priced_quotes():
    """Quotes built at a known forward must imply that forward back."""
    from skewmap.compute import implied_forward, pricing_spot
    true_spot, r, t = 217.23, R, T
    calls, puts = [], []
    for k in (205.0, 210.0, 215.0, 220.0, 225.0, 230.0):
        c = bs_price("call", true_spot, k, t, r, 0.35)
        p = bs_price("put", true_spot, k, t, r, 0.35)
        calls.append({"strike": k, "bid": c - 0.05, "ask": c + 0.05})
        puts.append({"strike": k, "bid": p - 0.05, "ask": p + 0.05})
    fwd = implied_forward(calls, puts, t, r, spot_hint=true_spot)
    assert abs(fwd - true_spot * math.exp(r * t)) < 0.05

    # A stale feed spot is corrected back to what the chain actually implies.
    stale = 227.98
    eff, drift = pricing_spot(calls, puts, stale, t, r)
    assert abs(eff - true_spot) < 0.05
    assert drift < -0.04                      # the feed was ~5% too high


def test_a_stale_spot_manufactures_skew_and_the_forward_removes_it():
    """The NVDA bug: a spot a session stale lifts put IV and drops call IV, so
    a flat smile reads as heavy put skew. Pricing off the chain's own forward
    puts both sides back on the same footing."""
    from skewmap.compute import pricing_spot
    true_spot, flat_iv, stale = 217.23, 0.35, 227.98
    calls, puts = [], []
    for k in (200.0, 210.0, 217.0, 225.0, 235.0):
        c = bs_price("call", true_spot, k, T, R, flat_iv)
        p = bs_price("put", true_spot, k, T, R, flat_iv)
        calls.append({"strike": k, "bid": c - 0.02, "ask": c + 0.02})
        puts.append({"strike": k, "bid": p - 0.02, "ask": p + 0.02})

    wing_p, wing_c = puts[0], calls[-1]        # 200 put, 235 call
    bad_p = implied_vol("put", mid_price(wing_p), stale, 200.0, T, R)
    bad_c = implied_vol("call", mid_price(wing_c), stale, 235.0, T, R)
    assert bad_p - bad_c > 0.10                # fake skew, over 10 vol points

    eff, _ = pricing_spot(calls, puts, stale, T, R)
    good_p = implied_vol("put", mid_price(wing_p), eff, 200.0, T, R)
    good_c = implied_vol("call", mid_price(wing_c), eff, 235.0, T, R)
    assert abs(good_p - flat_iv) < 0.01 and abs(good_c - flat_iv) < 0.01
    assert abs(good_p - good_c) < 0.01         # flat smile reads flat


def test_forward_refuses_when_the_chain_cannot_support_it():
    from skewmap.compute import implied_forward, pricing_spot
    thin = [{"strike": 100.0, "bid": 1.0, "ask": 1.2}]
    assert implied_forward(thin, thin, T, R, spot_hint=100.0) is None
    assert implied_forward([], [], T, R) is None
    # No forward means the fetched spot stands, and drift is reported as unknown.
    assert pricing_spot([], [], 100.0, T, R) == (100.0, None)
