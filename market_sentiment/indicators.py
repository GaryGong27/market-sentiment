"""Derived indicators computed from raw close series.

A Series is a list of (date_str, float) sorted ascending. These helpers avoid
pandas/numpy; the data volumes (a few hundred daily points) are tiny.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

Series = List[Tuple[str, float]]


def latest(series: Series) -> Optional[float]:
    return series[-1][1] if series else None


def latest_date(series: Series) -> Optional[str]:
    return series[-1][0] if series else None


def values(series: Series) -> List[float]:
    return [v for _, v in series]


def sma(series: Series, window: int) -> Optional[float]:
    vals = values(series)
    if len(vals) < window:
        return None
    return sum(vals[-window:]) / window


def pct_change_over(series: Series, window: int) -> Optional[float]:
    """Percent change between the close `window` points ago and the latest."""
    vals = values(series)
    if len(vals) <= window or vals[-window - 1] == 0:
        return None
    return (vals[-1] / vals[-window - 1] - 1.0) * 100.0


def diff_over(series: Series, window: int) -> Optional[float]:
    """Absolute change (latest minus `window` points ago). For bps/yields."""
    vals = values(series)
    if len(vals) <= window:
        return None
    return vals[-1] - vals[-window - 1]


def drawdown_from_high(series: Series, window: int) -> Optional[float]:
    """Percent below the trailing `window`-day high (negative number)."""
    vals = values(series)
    if len(vals) < 2:
        return None
    window_vals = vals[-window:] if len(vals) >= window else vals
    high = max(window_vals)
    if high == 0:
        return None
    return (vals[-1] / high - 1.0) * 100.0


def consecutive_above(series: Series, threshold: float) -> int:
    """Count of trailing consecutive closes >= threshold."""
    count = 0
    for _, v in reversed(series):
        if v >= threshold:
            count += 1
        else:
            break
    return count


def consecutive_within(series: Series, low: float, high: float) -> int:
    count = 0
    for _, v in reversed(series):
        if low <= v <= high:
            count += 1
        else:
            break
    return count


def peak_drop(series: Series, window: int) -> Optional[float]:
    """How far (in raw points) the latest value is below its trailing high.

    Used for VIX 'falling from peak': returns trailing_high - latest.
    """
    vals = values(series)
    if not vals:
        return None
    window_vals = vals[-window:] if len(vals) >= window else vals
    return max(window_vals) - vals[-1]


def minmax_score(series: Series, window: int, invert: bool = False) -> Optional[float]:
    """Map the latest value to 0-100 by its position in the trailing window.

    0 = window minimum, 100 = window maximum. `invert=True` flips it so that
    a *low* raw value scores *high* (used when low = greed, e.g. put/call, VIX).
    """
    vals = values(series)
    if len(vals) < 5:
        return None
    w = vals[-window:] if len(vals) >= window else vals
    lo, hi = min(w), max(w)
    if hi == lo:
        return 50.0
    score = (vals[-1] - lo) / (hi - lo) * 100.0
    return 100.0 - score if invert else score


def align(a: Series, b: Series) -> Tuple[List[float], List[float]]:
    """Inner-join two series on date, returning aligned value lists."""
    bd = dict(b)
    av, bv = [], []
    for d, v in a:
        if d in bd:
            av.append(v)
            bv.append(bd[d])
    return av, bv


# ---------------------------------------------------------------------------
# CNN Fear & Greed reconstruction (0 = extreme fear, 100 = extreme greed)
# ---------------------------------------------------------------------------

def _ratio_series(a: Series, b: Series) -> Series:
    """Date-aligned ratio a/b as a Series (for relative-strength components)."""
    bd = dict(b)
    out: Series = []
    for d, v in a:
        if d in bd and bd[d]:
            out.append((d, v / bd[d]))
    return out


def _diff_series(a: Series, b: Series) -> Series:
    """Date-aligned difference a-b as a Series (dates preserved correctly)."""
    bd = dict(b)
    return [(d, v - bd[d]) for d, v in a if d in bd]


def fear_greed_components(data: Dict[str, Series], cfg: dict) -> Dict[str, float]:
    """Compute each available F&G sub-score (0-100 greed). Missing -> omitted."""
    win = cfg["norm_window"]
    enabled = cfg["components"]
    scores: Dict[str, float] = {}

    # 1. Momentum: SPY relative to its 125-day MA. Above MA = greed.
    if enabled.get("momentum") and data.get("SPY"):
        spy = data["SPY"]
        ma125 = sma(spy, 125)
        last = latest(spy)
        if ma125 and last:
            rel = (last / ma125 - 1.0) * 100.0  # % above/below MA
            # map roughly [-10%, +10%] -> [0, 100]
            scores["momentum"] = _clamp((rel + 10.0) / 20.0 * 100.0)

    # 2. Stock price strength: NYSE 52wk (highs - lows), normalised.
    if enabled.get("strength") and data.get("HIGHS") and data.get("LOWS"):
        net = _diff_series(data["HIGHS"], data["LOWS"])
        if net:
            s = minmax_score(net, win)
            if s is not None:
                scores["strength"] = s

    # 3. Put/Call: low ratio = greed (invert). 5-day average.
    if enabled.get("putcall") and data.get("PUTCALL"):
        pc = data["PUTCALL"]
        if len(pc) >= 5:
            avg5 = [(pc[i][0], sum(v for _, v in pc[i - 4:i + 1]) / 5.0)
                    for i in range(4, len(pc))]
            s = minmax_score(avg5, win, invert=True)
            if s is not None:
                scores["putcall"] = s

    # 4. Volatility: VIX vs its 50d MA. VIX above MA = fear (invert position).
    if enabled.get("volatility") and data.get("VIX"):
        vix = data["VIX"]
        s = minmax_score(vix, win, invert=True)  # high VIX -> low score (fear)
        if s is not None:
            scores["volatility"] = s

    # 5. Safe-haven demand: 20d SPY return minus 20d IEF (Treasury) return.
    if enabled.get("safe_haven") and data.get("SPY") and data.get("IEF"):
        spy_r = pct_change_over(data["SPY"], 20)
        ief_r = pct_change_over(data["IEF"], 20)
        if spy_r is not None and ief_r is not None:
            spread = spy_r - ief_r  # stocks beating bonds = greed
            scores["safe_haven"] = _clamp((spread + 10.0) / 20.0 * 100.0)

    # 6. Junk-bond demand: tight HY-vs-IG spread = greed. Use OAS difference.
    if enabled.get("junk_demand") and data.get("HY_OAS") and data.get("IG_OAS"):
        spread = _diff_series(data["HY_OAS"], data["IG_OAS"])
        if spread:
            s = minmax_score(spread, win, invert=True)  # narrow spread = greed
            if s is not None:
                scores["junk_demand"] = s

    return scores


def fear_greed_index(components: Dict[str, float]) -> Optional[float]:
    """Equal-weight average over whatever components were computed."""
    if not components:
        return None
    return sum(components.values()) / len(components)


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))
