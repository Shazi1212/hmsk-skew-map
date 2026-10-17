"""Yahoo serves the session in progress as a row with a real volume and no
close. Dropping that row rolls the whole board back a session while the option
chain stays current, which is how NVDA came to be priced at 227.98 against a
chain implying 217.15.
"""
from skewmap.providers import repaired_close

NAN = float("nan")


def test_a_real_close_is_left_alone():
    assert repaired_close(227.98, 217.55, 226.0) == 227.98


def test_a_missing_close_is_filled_from_the_live_price():
    # The real 2026-08-28 shape, for all three names checked that day.
    assert repaired_close(NAN, 217.55, 227.98) == 217.55     # NVDA
    assert repaired_close(NAN, 769.35, 771.10) == 769.35     # SPY
    assert repaired_close(NAN, 88.25, 89.05) == 88.25        # SO


def test_an_implausible_replacement_is_refused():
    # A missing session is a cheap failure. A wrong spot is not: it would move
    # every delta, every implied vol and the one-month return at once.
    assert repaired_close(NAN, 2.0, 228.0) is None
    assert repaired_close(NAN, 900.0, 228.0) is None
    assert repaired_close(NAN, 228.0 * 1.49, 228.0) == 228.0 * 1.49   # inside the band


def test_nothing_to_repair_with_gives_up_rather_than_guessing():
    assert repaired_close(NAN, None, 228.0) is None
    assert repaired_close(NAN, NAN, 228.0) is None
    assert repaired_close(NAN, 0.0, 228.0) is None
    assert repaired_close(NAN, -5.0, 228.0) is None
    assert repaired_close(NAN, 217.55, None) is None     # no reference to judge against
    assert repaired_close(NAN, 217.55, NAN) is None
