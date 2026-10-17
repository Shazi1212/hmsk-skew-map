"""RVOL has to survive being asked mid-session.

The board now rebuilds every half hour while the market is open, so the last
bar's volume is only part of a day. Compared against whole-day averages that
reads light for no reason but the clock, and the sentence generator would call
every name quiet all morning.
"""
from datetime import datetime, date, timezone, timedelta

from skewmap.compute import session_progress, volume_fraction, relative_volume

ET = timezone(timedelta(hours=-4))          # exchange local time, as the index carries it
DAY = date(2026, 8, 31)


def at(h, m):
    return datetime(2026, 8, 31, h, m, tzinfo=ET)


def test_progress_through_the_session():
    assert session_progress(at(9, 30), DAY) == 0.0
    assert abs(session_progress(at(12, 45), DAY) - 0.5) < 0.01
    assert abs(session_progress(at(10, 0), DAY) - 30 / 390) < 1e-9


def test_a_finished_session_has_no_progress():
    assert session_progress(at(16, 0), DAY) is None        # the close
    assert session_progress(at(18, 0), DAY) is None        # after hours
    assert session_progress(at(11, 0), date(2026, 8, 28)) is None   # an earlier bar
    assert session_progress(None, DAY) is None


def test_before_the_open_nothing_has_traded():
    assert session_progress(at(8, 0), DAY) == 0.0


def test_the_volume_curve_honours_its_anchors():
    assert abs(volume_fraction(0.0)) < 1e-9
    assert abs(volume_fraction(1.0) - 1.0) < 1e-9
    assert abs(volume_fraction(0.5) - 0.5) < 1e-9          # symmetric about midday
    half_hour = 30 / 390
    assert abs(volume_fraction(half_hour) - 0.13) < 0.005  # first 30 min ~13%
    assert abs(volume_fraction(1 - half_hour) - 0.87) < 0.005
    # Monotonic, or scaling could run backwards as the day goes on.
    xs = [i / 50 for i in range(51)]
    assert all(volume_fraction(b) > volume_fraction(a) for a, b in zip(xs, xs[1:]))


def test_a_straight_line_would_have_overstated_the_open():
    # The reason the curve exists: linear says 7.7% traded, reality says ~13%,
    # so linear scaling inflates RVOL by about two thirds at the open.
    half_hour = 30 / 390
    assert volume_fraction(half_hour) / half_hour > 1.6


def test_a_closed_session_is_counted_not_projected():
    rvol, provisional = relative_volume(150, [100] * 20, progress=None)
    assert rvol == 1.5 and provisional is False


def test_mid_session_volume_is_scaled_to_a_whole_day():
    # Half the day gone, half a normal day's volume traded: that is normal,
    # not quiet. Unscaled it would read 0.5 and the board would say "light".
    base = [100.0] * 20
    raw = 50 / 100
    rvol, provisional = relative_volume(50, base, progress=0.5)
    assert provisional is True
    assert abs(rvol - 1.0) < 0.01
    assert raw < 0.7 <= rvol       # crosses the "light" threshold it would have tripped


def test_too_early_to_say_returns_nothing():
    # Ten minutes in, scaling divides by about 0.05 and amplifies noise.
    rvol, provisional = relative_volume(5, [100.0] * 20, progress=10 / 390)
    assert rvol is None and provisional is True


def test_no_history_to_compare_against():
    assert relative_volume(100, [], progress=None) == (None, False)
    assert relative_volume(100, [0, 0], progress=None) == (None, False)
    assert relative_volume(None, [100.0] * 20, progress=None) == (None, False)


def test_a_genuinely_heavy_morning_still_reads_heavy():
    # Scaling must not flatten real signal: triple the normal pace by midday.
    rvol, _ = relative_volume(150, [100.0] * 20, progress=0.5)
    assert rvol > 2.9
