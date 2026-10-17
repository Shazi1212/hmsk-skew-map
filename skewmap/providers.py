"""Data providers behind one small interface.

Each provider exposes:
    name                      -> str, printed on the dashboard's LIVE badge
    price_context(ticker)     -> dict(spot, ret1m, rvol, session, handle)
    expiries(handle)          -> list of 'YYYY-MM-DD'
    chain(handle, expiry)     -> (calls, puts): lists of {strike, iv, oi, delta?}
                                 iv as a DECIMAL (0.35), delta optional
    next_earnings(handle)     -> 'YYYY-MM-DD' or None

Failures raise ProviderError with a human-readable reason. The pipeline
catches these per name and prints them by name, never silently.

Licensing note, read before showing the board to anyone else:
  * yfinance scrapes Yahoo's unofficial endpoints. Fine for building and
    for personal, educational use; not licensed for redistribution.
  * Tradier (optional, needs your own token) has its own terms; check that
    your tier allows what you plan to display.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone, timedelta

from .compute import bs_delta  # noqa: F401  (kept close for provider authors)
from .compute import session_progress, relative_volume


class ProviderError(Exception):
    pass


def repaired_close(last_close, last_price, prev_close, band: float = 0.5):
    """The close to use for a daily bar Yahoo has served without one.

    Yahoo returns the current session as a row carrying a real volume but a
    NaN close, and its fast_info holds that same session's actual price. The
    tempting fix is to drop the row, and it is wrong: it silently rolls the
    board back to the previous session while the option chain stays current,
    so every IV gets read against a spot that is a day out. That is the bug
    that made NVDA price at 227.98 against a chain implying 217.15.

    Returns the repaired close, or None when there is nothing trustworthy to
    put there. A replacement further than band from the previous close is
    refused: a wild value here would poison spot, the return and every delta
    at once, and a missing session is a much cheaper failure than a wrong one.
    """
    if last_close is not None and last_close == last_close:   # already a number
        return float(last_close)
    if last_price is None or last_price != last_price or last_price <= 0:
        return None
    if prev_close is None or prev_close != prev_close or prev_close <= 0:
        return None
    lo, hi = prev_close * (1.0 - band), prev_close * (1.0 + band)
    return float(last_price) if lo <= last_price <= hi else None


def _today_utc():
    return datetime.now(timezone.utc).date()


# ----------------------------------------------------------------------------
# yfinance: zero-setup, delayed, unofficial. Default for development and demos.
# ----------------------------------------------------------------------------
class YFinanceProvider:
    name = "yfinance delayed (unofficial Yahoo data, personal use)"

    def __init__(self, cfg):
        try:
            import yfinance as yf
        except ImportError as e:
            raise ProviderError("yfinance not installed; pip install -r requirements.txt") from e
        self._yf = yf
        self._pause = float(cfg.get("request_pause_sec", 0.4))
        self._min_progress = float((cfg.get("guards") or {})
                                   .get("min_session_progress_for_rvol", 0.15))

    def price_context(self, ticker: str):
        tk = self._yf.Ticker(ticker)
        h = tk.history(period="3mo", auto_adjust=True)
        if h is None or not len(h):
            raise ProviderError("no price history returned")

        # Repair the current session before anything reads it. Only reach for
        # fast_info when the close is actually missing, so the normal path
        # stays one request per name.
        spot_source = "daily close"
        if h["Close"].iloc[-1] != h["Close"].iloc[-1]:                  # NaN
            known = h["Close"].dropna()
            fixed = repaired_close(None, self._last_price(tk),
                                   float(known.iloc[-1]) if len(known) else None)
            if fixed is not None:
                h = h.copy()
                h.loc[h.index[-1], "Close"] = fixed
                spot_source = "intraday last price"

        # Anything still missing a close is genuinely unusable.
        h = h[h["Close"].notna()]
        if len(h) < 25:
            raise ProviderError("not enough price history (need ~25 sessions)")

        closes = [float(x) for x in h["Close"].tolist()]
        vols = [float(x) for x in h["Volume"].tolist()]
        spot = closes[-1]
        ret1m = (closes[-1] / closes[-22] - 1.0) * 100.0

        # The last bar's volume is only final once the session has closed. The
        # index carries the exchange timezone, so ask it what time it is there
        # rather than guessing from UTC and getting DST wrong twice a year.
        last = h.index[-1]
        now_local = datetime.now(last.tz) if getattr(last, "tz", None) else None
        progress = session_progress(now_local, last.date())
        rvol, provisional = relative_volume(vols[-1], vols[-21:-1], progress,
                                            float(self._min_progress))

        session = last.date().isoformat()
        time.sleep(self._pause)
        return {"spot": spot, "ret1m": ret1m, "rvol": rvol, "session": session,
                "spot_source": spot_source, "rvol_provisional": provisional,
                "session_progress": round(progress, 3) if progress is not None else None,
                "handle": tk}

    @staticmethod
    def _last_price(tk):
        try:
            return float(tk.fast_info.last_price)
        except Exception:  # noqa: BLE001  fast_info is best effort
            return None

    def expiries(self, handle):
        return list(handle.options or [])

    def chain(self, handle, expiry: str):
        oc = handle.option_chain(expiry)

        def conv(df):
            def num(v):  # NaN and None both mean "not quoted"
                return float(v) if v is not None and v == v else None

            out = []
            for _, row in df.iterrows():
                oi = row.get("openInterest")
                vol = row.get("volume")
                out.append({
                    "strike": float(row["strike"]),
                    "iv": num(row.get("impliedVolatility")),
                    "oi": int(oi) if oi == oi and oi is not None else 0,
                    # Yahoo's impliedVolatility is derived from the last trade,
                    # which on an untraded strike can be days stale or pure
                    # fiction. The quotes are what say whether a market exists.
                    "bid": num(row.get("bid")),
                    "ask": num(row.get("ask")),
                    "volume": int(vol) if vol == vol and vol is not None else 0,
                })
            return out

        time.sleep(self._pause)
        return conv(oc.calls), conv(oc.puts)

    def next_earnings(self, handle):
        try:
            df = handle.get_earnings_dates(limit=12)
            if df is None or df.empty:
                return None
            today = _today_utc()
            future = sorted(ts.date() for ts in df.index if ts.date() >= today)
            return future[0].isoformat() if future else None
        except Exception:
            return None


# ----------------------------------------------------------------------------
# Tradier: documented REST API, greeks included (delta and mid IV via ORATS).
# Set TRADIER_TOKEN. Set TRADIER_ENV=sandbox to hit the sandbox host.
# Written to the published API docs and never run against a real key;
# verify it with a sandbox token before relying on it.
# ----------------------------------------------------------------------------
class TradierProvider:
    name = "Tradier delayed"

    def __init__(self, cfg):
        import requests
        self._rq = requests
        token = os.environ.get("TRADIER_TOKEN")
        if not token:
            raise ProviderError("TRADIER_TOKEN is not set")
        env = os.environ.get("TRADIER_ENV", "production")
        self._base = "https://sandbox.tradier.com/v1" if env == "sandbox" else "https://api.tradier.com/v1"
        self._h = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        self._pause = float(cfg.get("request_pause_sec", 0.4))

    def _get(self, path, **params):
        r = self._rq.get(self._base + path, headers=self._h, params=params, timeout=20)
        if r.status_code != 200:
            raise ProviderError(f"tradier {path} -> HTTP {r.status_code}")
        time.sleep(self._pause)
        return r.json()

    def price_context(self, ticker: str):
        end = _today_utc()
        start = end - timedelta(days=120)
        j = self._get("/markets/history", symbol=ticker, interval="daily",
                      start=start.isoformat(), end=end.isoformat())
        days = ((j.get("history") or {}).get("day")) or []
        if isinstance(days, dict):
            days = [days]
        if len(days) < 25:
            raise ProviderError("not enough price history")
        closes = [float(d["close"]) for d in days]
        vols = [float(d.get("volume") or 0) for d in days]
        spot = closes[-1]
        ret1m = (closes[-1] / closes[-22] - 1.0) * 100.0
        base = vols[-21:-1]
        avg = (sum(base) / len(base)) if base else 0.0
        rvol = (vols[-1] / avg) if avg > 0 else None
        session = str(days[-1]["date"])
        return {"spot": spot, "ret1m": ret1m, "rvol": rvol, "session": session, "handle": ticker}

    def expiries(self, handle):
        j = self._get("/markets/options/expirations", symbol=handle,
                      includeAllRoots="true", strikes="false")
        dates = ((j.get("expirations") or {}).get("date")) or []
        return [dates] if isinstance(dates, str) else list(dates)

    def chain(self, handle, expiry: str):
        j = self._get("/markets/options/chains", symbol=handle,
                      expiration=expiry, greeks="true")
        opts = ((j.get("options") or {}).get("option")) or []
        if isinstance(opts, dict):
            opts = [opts]
        calls, puts = [], []
        for o in opts:
            g = o.get("greeks") or {}
            iv = g.get("mid_iv") or g.get("smv_vol")
            row = {
                "strike": float(o["strike"]),
                "iv": float(iv) if iv else None,
                "oi": int(o.get("open_interest") or 0),
                "delta": float(g["delta"]) if g.get("delta") is not None else None,
                "bid": float(o["bid"]) if o.get("bid") is not None else None,
                "ask": float(o["ask"]) if o.get("ask") is not None else None,
                "volume": int(o.get("volume") or 0),
            }
            (calls if o.get("option_type") == "call" else puts).append(row)
        return calls, puts

    def next_earnings(self, handle):
        return None  # unreliable endpoint; use earnings_overrides in config.yaml


# ----------------------------------------------------------------------------
# Demo: the exact sample board from the workbench, no network. Used by --demo,
# CI, and the dashboard smoke tests, so the whole path stays testable offline.
# ----------------------------------------------------------------------------
class DemoProvider:
    name = "demo synthetic board (no market data)"

    _ROWS = [
        ("NVDA", 38.0, 41.5, 36.8, 8.2, 1.3, 9),
        ("AMD", 44.0, 46.0, 43.1, 12.5, 1.8, None),
        ("AVGO", 33.0, 35.2, 32.0, 9.8, 1.1, None),
        ("MU", 47.0, 45.2, 47.9, -6.8, 1.6, 40),
        ("PLTR", 52.0, 50.5, 53.2, 15.4, 2.1, None),
        ("CRM", 31.0, 33.0, 30.2, -4.2, 1.0, 5),
        ("DDOG", 45.0, 44.0, 45.8, -9.5, 1.1, None),
        ("XOM", 22.0, 24.0, 21.1, -3.5, 0.8, None),
        ("OXY", 29.0, 31.5, 27.6, -7.9, 1.4, None),
        ("SLB", 28.0, 29.0, 27.5, -1.2, 0.7, None),
        ("JPM", 19.0, 20.4, 18.3, 4.6, 1.0, None),
        ("GS", 23.0, 22.4, 23.6, 6.9, 1.2, None),
        ("SCHW", 25.0, 26.1, 24.4, 1.8, 0.6, None),
        ("COIN", 62.0, 58.5, 63.4, -11.6, 1.9, None),
        ("DUK", 15.0, 8.0, 29.5, 1.4, 0.9, None),
        ("NEE", 21.0, 22.3, 20.1, -2.1, 0.9, None),
        ("SO", 16.0, 17.1, 15.9, 0.8, 0.7, None),
        ("IONQ", 78.0, 71.0, 82.0, -14.2, 2.4, None, False),
    ]

    def __init__(self, cfg):
        self._cfg = cfg

    def spy_1m(self):
        return 2.4

    def rows(self):
        today = _today_utc()
        out = []
        for r in self._ROWS:
            t, atm, piv, civ, ret, rvol, edays = r[:7]
            chain_ok = r[7] if len(r) > 7 else True
            earn = (today + timedelta(days=edays)).isoformat() if edays else None
            out.append({"t": t, "atm": atm, "put": piv, "call": civ, "ret": ret,
                        "rvol": rvol, "earn": earn, "chain": chain_ok,
                        "meta": {"expiry": None, "dte": None,
                                 "note": "synthetic demo values" if chain_ok else "synthetic; flagged thin"}})
        return out


def make_provider(cfg):
    name = (cfg.get("provider") or "yfinance").lower()
    if name == "tradier":
        return TradierProvider(cfg)
    if name == "demo":
        return DemoProvider(cfg)
    return YFinanceProvider(cfg)
