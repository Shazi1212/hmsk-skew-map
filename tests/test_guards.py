from skewmap.compute import quadrant
from skewmap.fetch import diff_guard, quadrant_flips


def _board(sess="2026-08-31", chain=True):
    return {"session_date": sess, "rows": [{"t": "NVDA", "chain": chain}]}


def test_quadrant_mapping_matches_dashboard():
    assert quadrant(0.10, 5.0, 0.8) == "HEDGE"
    assert quadrant(-0.10, 5.0, 0.8) == "CHASE"
    assert quadrant(0.10, -5.0, 0.8) == "FEAR"
    assert quadrant(-0.10, -5.0, 0.8) == "CONTRA"
    assert quadrant(0.0, 0.0, 0.8) == "HEDGE"  # >= boundaries, same as quadOf()
    assert quadrant(0.9, 5.0, 0.8) is None     # past the sanity ceiling
    assert quadrant(None, 5.0, 0.8) is None
    assert quadrant(0.1, None, 0.8) is None


def test_diff_guard_fires_on_one_session_jump():
    hist = {"NVDA": [
        {"date": "2026-08-28", "skew": 0.05, "pts": 2.0, "ret": 4.0, "ok": True},
        {"date": "2026-08-31", "skew": 0.66, "pts": 20.0, "ret": 4.0, "ok": True},
    ]}
    w = diff_guard(_board(), hist, 0.5)
    assert len(w) == 1 and w[0]["t"] == "NVDA" and "one session" in w[0]["reason"]
    assert diff_guard(_board(), hist, 0.7) == []


def test_diff_guard_needs_a_previous_session():
    hist = {"NVDA": [{"date": "2026-08-31", "skew": 0.66, "pts": 20.0}]}
    assert diff_guard(_board(), hist, 0.5) == []


def test_quadrant_flip_detected():
    hist = {"NVDA": [
        {"date": "2026-08-28", "skew": -0.06, "pts": -2.0, "ret": 4.0, "ok": True},
        {"date": "2026-08-31", "skew": 0.07, "pts": 2.5, "ret": 4.0, "ok": True},
    ]}
    assert quadrant_flips(_board(), hist, 0.8) == [{
        "t": "NVDA", "from": "CHASE", "to": "HEDGE",
        "from_date": "2026-08-28", "date": "2026-08-31",
    }]


def test_pre_upgrade_history_and_thin_days_do_not_flip():
    old_shape = {"NVDA": [
        {"date": "2026-08-28", "skew": -0.06, "pts": -2.0},  # no ret/ok fields
        {"date": "2026-08-31", "skew": 0.07, "pts": 2.5, "ret": 4.0, "ok": True},
    ]}
    assert quadrant_flips(_board(), old_shape, 0.8) == []
    thin_today = {"NVDA": [
        {"date": "2026-08-28", "skew": -0.06, "pts": -2.0, "ret": 4.0, "ok": True},
        {"date": "2026-08-31", "skew": 0.07, "pts": 2.5, "ret": 4.0, "ok": False},
    ]}
    assert quadrant_flips(_board(), thin_today, 0.8) == []
