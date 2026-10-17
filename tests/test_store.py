from skewmap.store import rows_for_db, _date_or_none


def test_rows_for_db_shapes_and_computes_skew():
    board = {
        "session_date": "2026-08-28",
        "rows": [{
            "t": "NVDA", "sector": "Semis", "atm": 40.0, "put": 44.0, "call": 38.0,
            "ret": 8.2, "rvol": 1.3, "earn": "2026-09-08", "chain": True,
            "watch": False, "meta": {"expiry": "2026-10-16"},
        }],
    }
    (row,) = rows_for_db(board)
    sess, t, sector, atm, put_iv, call_iv, sk, pts, ret, rvol, earn, ok, watch, meta = row
    assert (sess, t, sector) == ("2026-08-28", "NVDA", "Semis")
    assert (put_iv, call_iv) == (44.0, 38.0)
    assert sk == 0.15 and pts == 6.0
    assert earn == "2026-09-08" and ok is True and watch is False
    assert '"expiry": "2026-10-16"' in meta


def test_rows_for_db_never_invents_values():
    board = {"session_date": "2026-08-28",
             "rows": [{"t": "X", "atm": 0, "put": None, "call": 38.0,
                       "earn": "next week", "chain": False}]}
    (row,) = rows_for_db(board)
    assert row[6] is None and row[7] is None      # skew, pts unknowable
    assert row[10] is None                        # bad earnings string stays out
    assert _date_or_none("2026-13-40") is None
