"""Decision 5, the two-sided market gate.

The regression case is real. On 2026-08-28 the SO board printed a skew of
-1.48 because the nearest-25-delta call was the 100 strike, quoted bid 0.00 /
ask 2.25 with a Yahoo IV of 47.9. There was no market on it at all; the IV was
fiction, and one leg inverted the whole name. The day before, the same rule
had picked the 95 strike and printed -0.10.
"""
from skewmap.compute import quote_ok, select_leg, quality_check, chain_stats

Q = {"require_bid": True, "max_leg_spread_pct": 0.35, "min_strikes_per_side": 3,
     "min_leg_open_interest": 0, "max_delta_gap": 0.10}

# SO calls, 2026-10-16 expiry, spot 89.05, as Yahoo served them.
SO_CALLS = [
    {"strike": 85.0, "iv": 0.317, "oi": 5, "bid": 4.30, "ask": 5.80},
    {"strike": 87.5, "iv": 0.213, "oi": 30, "bid": 2.55, "ask": 3.10},
    {"strike": 90.0, "iv": 0.188, "oi": 520, "bid": 1.45, "ask": 1.65},
    {"strike": 92.5, "iv": 0.203, "oi": 116, "bid": 0.65, "ask": 1.05},
    {"strike": 95.0, "iv": 0.208, "oi": 1263, "bid": 0.20, "ask": 0.60},
    {"strike": 97.5, "iv": 0.272, "oi": 72, "bid": 0.00, "ask": 0.75},
    {"strike": 100.0, "iv": 0.479, "oi": 36, "bid": 0.00, "ask": 2.25},
]


def test_no_bid_is_not_a_price():
    ok, why = quote_ok({"bid": 0.0, "ask": 2.25})
    assert not ok and why == "no bid"


def test_wide_spread_is_not_a_price():
    ok, why = quote_ok({"bid": 0.20, "ask": 0.60})   # 100% of mid
    assert not ok and "spread" in why
    assert quote_ok({"bid": 1.45, "ask": 1.65})[0]   # 12.9% of mid, fine


def test_rows_without_quotes_cannot_be_judged():
    # Providers that ship no bid/ask (and the demo board) must still work.
    assert quote_ok({"strike": 90.0, "iv": 0.2})[0]


def test_require_bid_can_be_turned_off_but_spread_still_applies():
    assert quote_ok({"bid": 0.0, "ask": 0.10}, require_bid=False)[0] is False  # spread 200%
    assert quote_ok({"bid": 0.0, "ask": 2.25}, require_bid=False, max_spread_pct=0)[0]


# Spot on the session that broke: at 92 the nearest-25-delta call is the 97.5
# strike, quoted 0.00 / 0.75. Same shape as the 100 strike that actually landed
# on the board, and it reproduces inside the chain captured above.
SPOT, T, R = 92.0, 49 / 365, 0.045
NO_MARKET = {97.5, 100.0}      # no bid at all
WIDE = {85.0, 92.5, 95.0}      # quoted, but the spread is 30% to 100% of mid


def test_ungated_selection_picks_a_strike_with_no_bid():
    leg = select_leg(SO_CALLS, "call", SPOT, T, R, 0.25)
    assert leg["strike"] in NO_MARKET
    assert leg["iv"] > 25.0        # the fiction that inverted the name


def test_gated_selection_never_picks_an_unquoted_or_wide_strike():
    leg = select_leg(SO_CALLS, "call", SPOT, T, R, 0.25, Q)
    assert leg["strike"] not in NO_MARKET | WIDE
    assert leg["spread_pct"] <= Q["max_leg_spread_pct"]


def test_so_is_flagged_rather_than_rescued():
    """The gate does not invent a good 25-delta call where none trades.

    Only three near-the-money strikes survive, so the best tradeable leg sits
    far from the target delta, max_delta_gap catches that, and the name lands
    on the board flagged thin and excluded from sector math. Refusing to read
    SO is the correct answer, not a worse one.
    """
    leg = select_leg(SO_CALLS, "call", SPOT, T, R, 0.25, Q)
    assert leg["gap"] > Q["max_delta_gap"]
    puts = [{"strike": 85.0, "iv": 0.19, "oi": 799, "bid": 0.90, "ask": 1.10}]
    reasons = quality_check(SO_CALLS, puts, None, leg, Q)
    assert any("delta" in r and "gap" in r for r in reasons)
    assert reasons, "a chain this thin must never come back clean"


def test_quality_bar_counts_only_tradeable_strikes():
    puts = [{"strike": 85.0, "iv": 0.19, "oi": 799, "bid": 0.90, "ask": 1.10}]
    stats = chain_stats(SO_CALLS, puts, Q)
    assert stats["call_quoted"] == 7 and stats["call_tradeable"] == 3
    assert any("real market" in r for r in quality_check(SO_CALLS, puts, None, None, Q))


def test_gate_is_off_when_no_quality_config_is_passed():
    # Existing callers and the demo board keep their old behaviour exactly.
    assert select_leg(SO_CALLS, "call", SPOT, T, R, 0.25)["strike"] in NO_MARKET
