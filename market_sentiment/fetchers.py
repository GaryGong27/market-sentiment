"""Keyless data fetchers: Yahoo chart API, FRED CSV, CBOE put/call.

No API keys, no pandas/yfinance/fredapi. Every fetch returns a Series
(list of (date_str, float)) sorted ascending by date, or raises FetchError.
Callers decide whether a missing optional source is fatal.
"""

from __future__ import annotations

import csv
import io
import json
import os
import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

import requests

Series = List[Tuple[str, float]]

YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv"
# CBOE total put/call ratio (daily, historical CSV). Public, occasionally moves.
CBOE_PUTCALL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/total_pc.csv"

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


class FetchError(RuntimeError):
    """Raised when a required data source cannot be retrieved."""


def _cache_path(key: str) -> str:
    base = os.path.join(os.path.dirname(__file__), "..", ".cache")
    os.makedirs(base, exist_ok=True)
    safe = "".join(c if c.isalnum() else "_" for c in key)
    return os.path.join(base, f"{safe}.json")


def _cache_get(key: str, max_age_min: int) -> Optional[Series]:
    if max_age_min <= 0:
        return None
    path = _cache_path(key)
    if not os.path.exists(path):
        return None
    age_min = (time.time() - os.path.getmtime(path)) / 60.0
    if age_min > max_age_min:
        return None
    try:
        with open(path) as fh:
            return [(d, float(v)) for d, v in json.load(fh)]
    except Exception:
        return None


def _cache_put(key: str, series: Series) -> None:
    try:
        with open(_cache_path(key), "w") as fh:
            json.dump(series, fh)
    except Exception:
        pass  # cache is best-effort


def _get(url: str, params: dict, timeout: int, retries: int) -> requests.Response:
    last = None
    for attempt in range(max(1, retries)):
        try:
            resp = requests.get(
                url, params=params, headers={"User-Agent": _UA}, timeout=timeout
            )
            if resp.status_code == 200:
                return resp
            last = FetchError(f"HTTP {resp.status_code} for {url}")
        except requests.RequestException as exc:  # network/timeout
            last = FetchError(f"{type(exc).__name__}: {exc}")
        time.sleep(0.6 * (attempt + 1))
    raise last or FetchError(f"failed: {url}")


def _epoch_to_date(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def yahoo(symbol: str, rng: str, timeout: int, retries: int,
          cache_min: int) -> Series:
    """Daily close series from Yahoo's chart endpoint."""
    key = f"yahoo_{symbol}_{rng}"
    cached = _cache_get(key, cache_min)
    if cached:
        return cached
    resp = _get(
        YAHOO_CHART.format(symbol=symbol),
        {"range": rng, "interval": "1d"},
        timeout,
        retries,
    )
    try:
        result = resp.json()["chart"]["result"][0]
        stamps = result["timestamp"]
        closes = result["indicators"]["quote"][0]["close"]
    except (KeyError, IndexError, TypeError) as exc:
        raise FetchError(f"unexpected Yahoo payload for {symbol}: {exc}")
    out: Series = [
        (_epoch_to_date(t), float(c))
        for t, c in zip(stamps, closes)
        if c is not None
    ]
    if not out:
        raise FetchError(f"no valid closes for {symbol}")
    out.sort(key=lambda x: x[0])
    _cache_put(key, out)
    return out


def fred(series_id: str, rng: str, timeout: int, retries: int,
         cache_min: int, scale: float = 1.0) -> Series:
    """Daily series from FRED's keyless fredgraph CSV.

    ``scale`` converts units (e.g. OAS percent -> basis points with scale=100).
    """
    key = f"fred_{series_id}_{rng}_x{scale}"
    cached = _cache_get(key, cache_min)
    if cached:
        return cached
    start = _range_to_start(rng)
    params = {"id": series_id}
    if start:
        params["cosd"] = start
    resp = _get(FRED_CSV, params, timeout, retries)
    out: Series = []
    reader = csv.reader(io.StringIO(resp.text))
    header = next(reader, None)
    for row in reader:
        if len(row) < 2:
            continue
        date_str, raw = row[0], row[1]
        if raw in (".", "", "NA"):
            continue
        try:
            out.append((date_str, float(raw) * scale))
        except ValueError:
            continue
    if not out:
        raise FetchError(f"no valid observations for FRED {series_id}")
    out.sort(key=lambda x: x[0])
    _cache_put(key, out)
    return out


def cboe_putcall(timeout: int, retries: int, cache_min: int) -> Series:
    """CBOE total put/call ratio. Optional; callers tolerate failure."""
    key = "cboe_total_pc"
    cached = _cache_get(key, cache_min)
    if cached:
        return cached
    resp = _get(CBOE_PUTCALL, {}, timeout, retries)
    out: Series = []
    reader = csv.reader(io.StringIO(resp.text))
    for row in reader:
        if len(row) < 5:
            continue
        date_raw = row[0].strip()
        ratio_raw = row[-1].strip()  # P/C ratio is the last column
        date_str = _parse_loose_date(date_raw)
        if not date_str:
            continue
        try:
            out.append((date_str, float(ratio_raw)))
        except ValueError:
            continue
    if not out:
        raise FetchError("no valid CBOE put/call rows")
    out.sort(key=lambda x: x[0])
    _cache_put(key, out)
    return out


def _parse_loose_date(s: str) -> Optional[str]:
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _range_to_start(rng: str) -> Optional[str]:
    rng = rng.strip().lower()
    try:
        n = int(rng[:-1])
    except ValueError:
        return None
    unit = rng[-1]
    days = {"y": 365, "m": 31, "d": 1}.get(unit)
    if not days:
        return None
    start = datetime.now(tz=timezone.utc) - timedelta(days=n * days + 5)
    return start.strftime("%Y-%m-%d")
