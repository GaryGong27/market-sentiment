"""The corrected sentiment / capital-allocation rule engine.

Corrections vs the original framework:
  * CREDIT IS THE MASTER GATE. High-yield OAS (duration-neutral), not bond-ETF
    price, decides systemic risk and overrides every buy signal. Resolves the
    S3/S4 overlap deterministically and the 2022 false-positive (ETF prices
    fell on rates, not credit).
  * VIX 30-35 GAP CLOSED. S2 (scale-in) spans VIX tense_low..extreme_low; S3
    (strong buy) takes over at extreme_low. No uncovered band.
  * REGIME DETECTION via the real yield (DFII10): rising real yields => inflation
    regime where stock-bond correlation is positive and cross-asset rules are
    unreliable.
  * CIRCUIT BREAKER guards against 2008-style structural declines (deep
    drawdown + widening credit stress) where "VIX 35, credit not frozen yet"
    historically kept falling.
  * CONFIRMATION WINDOWS: VIX triggers require N consecutive closes; the S5
    sell lean requires sustained complacency. Defeats 1-day vol spikes.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from . import indicators as ind

Series = List[Tuple[str, float]]


def _round(x, n=2):
    return round(x, n) if isinstance(x, (int, float)) else x


def evaluate(data: Dict[str, Series], cfg: dict) -> dict:
    signals = _compute_signals(data, cfg)
    regime = _regime(signals, cfg)
    credit = _credit_state(signals, cfg)
    breaker = _circuit_breaker(signals, credit, cfg)
    scenario = _classify(signals, credit, breaker, cfg)
    deployment = _deployment(signals, credit, scenario, breaker, cfg)
    return {
        "signals": signals,
        "regime": regime,
        "credit": credit,
        "circuit_breaker": breaker,
        "scenario": scenario,
        "deployment": deployment,
    }


# ---------------------------------------------------------------------------
# Signal computation
# ---------------------------------------------------------------------------

def _compute_signals(data: Dict[str, Series], cfg: dict) -> dict:
    vix = data.get("VIX", [])
    spy = data.get("SPY", [])
    s: dict = {}

    # --- VIX ---
    s["vix"] = ind.latest(vix)
    s["vix_ma"] = ind.sma(vix, cfg["vix"]["ma_window"])
    cdays = cfg["vix"]["confirmation_days"]
    s["vix_confirm_tense"] = ind.consecutive_above(vix, cfg["vix"]["tense_low"]) >= cdays
    s["vix_confirm_panic"] = ind.consecutive_above(vix, cfg["vix"]["panic_low"]) >= cdays
    s["vix_confirm_extreme"] = ind.consecutive_above(vix, cfg["vix"]["extreme_low"]) >= cdays
    s["vix_peak_drop"] = ind.peak_drop(vix, cfg["cross_asset"]["regime_window"])
    # Sustained complacency for the S5 sell lean: VIX held below calm_low+3 (~15)
    # for sustained_days. (F&G persistence can't be checked - we only reconstruct
    # the current value, not a history - so S5 also flags "confirm persistence".)
    sell_vix = cfg["vix"]["calm_low"] + 3
    s["vix_calm_sustained"] = (
        ind.consecutive_within(vix, 0, sell_vix) >= cfg["fear_greed"]["sustained_days"]
    )

    # --- Fear & Greed (reconstructed) ---
    fg_components = ind.fear_greed_components(data, cfg["fear_greed"])
    fg_value = ind.fear_greed_index(fg_components)
    s["fg_components"] = {k: _round(v, 1) for k, v in fg_components.items()}
    s["fg"] = _round(fg_value, 1) if fg_value is not None else None
    s["fg_label"] = _fg_label(fg_value, cfg["fear_greed"]) if fg_value is not None else None

    # --- Credit (OAS primary) ---
    hy = data.get("HY_OAS", [])
    s["oas"] = ind.latest(hy)  # basis points
    s["oas_change"] = ind.diff_over(hy, cfg["credit"]["rapid_widen_days"])
    s["ig_oas"] = ind.latest(data.get("IG_OAS", []))
    s["hyg_change"] = ind.pct_change_over(data.get("HYG", []), cfg["credit"]["rapid_widen_days"])
    s["jnk_change"] = ind.pct_change_over(data.get("JNK", []), cfg["credit"]["rapid_widen_days"])

    # --- Index drawdown ---
    dd_sym = cfg["drawdown"]["index_symbol"]
    s["drawdown"] = ind.drawdown_from_high(data.get(dd_sym, spy), cfg["drawdown"]["high_window"])

    # --- Breadth (coarse ETF-ratio proxies) ---
    rsp_spy = _ratio(data.get("RSP"), data.get("SPY"))
    iwm_spy = _ratio(data.get("IWM"), data.get("SPY"))
    bwin = cfg["breadth"]["ratio_window"]
    s["rsp_spy_change"] = ind.pct_change_over(rsp_spy, bwin) if rsp_spy else None
    s["iwm_spy_change"] = ind.pct_change_over(iwm_spy, bwin) if iwm_spy else None
    div = cfg["breadth"]["divergence_pct"]
    s["breadth_narrow"] = bool(
        (s["rsp_spy_change"] is not None and s["rsp_spy_change"] < -div)
        or (s["iwm_spy_change"] is not None and s["iwm_spy_change"] < -div)
    )

    # --- Cross-asset ---
    mwin = cfg["cross_asset"]["move_window"]
    ten_y = data.get("DGS10", [])
    s["y10"] = ind.latest(ten_y)
    s["y10_change_bps"] = ind.diff_over(ten_y, mwin)
    s["y10_change_bps"] = s["y10_change_bps"] * 100 if s["y10_change_bps"] is not None else None
    real = data.get("DFII10", [])
    s["real_yield"] = ind.latest(real)
    ryc = ind.diff_over(real, cfg["cross_asset"]["regime_window"])
    s["real_yield_change_bps"] = ryc * 100 if ryc is not None else None
    s["dxy_change"] = ind.pct_change_over(data.get("DXY", []), mwin)
    s["gold_change"] = ind.pct_change_over(data.get("GLD", []), mwin)

    # asof dates per source for freshness reporting
    s["asof"] = {k: ind.latest_date(v) for k, v in data.items() if v}
    return s


def _ratio(a: Optional[Series], b: Optional[Series]) -> Optional[Series]:
    if not a or not b:
        return None
    bd = dict(b)
    out: Series = [(d, v / bd[d]) for d, v in a if d in bd and bd[d]]
    return out or None


# ---------------------------------------------------------------------------
# Regime
# ---------------------------------------------------------------------------

def _regime(s: dict, cfg: dict) -> dict:
    rise = cfg["cross_asset"]["real_yield_rise_bps"]
    chg = s.get("real_yield_change_bps")
    if chg is None:
        return {"name": "unknown", "detail": "real-yield data unavailable"}
    if chg > rise:
        return {
            "name": "inflation_rates",
            "label": "Inflation / rates regime (A)",
            "real_yield_change_bps": _round(chg, 0),
            "detail": (
                "Real yields rising: stock-bond correlation likely POSITIVE. "
                "Bond-ETF credit signal is duration-contaminated; trust OAS only. "
                "'Wait for yield peak' rule buys late."
            ),
        }
    return {
        "name": "growth_risk",
        "label": "Growth / risk-off regime (B)",
        "real_yield_change_bps": _round(chg, 0),
        "detail": (
            "Real yields flat/falling: classic safe-haven dynamics apply, "
            "bonds hedge equities, cross-asset rules more reliable."
        ),
    }


# ---------------------------------------------------------------------------
# Credit (master gate)
# ---------------------------------------------------------------------------

def _credit_state(s: dict, cfg: dict) -> dict:
    c = cfg["credit"]
    oas = s.get("oas")
    chg = s.get("oas_change")
    rapid = chg is not None and chg > c["rapid_widen_bps"]
    if oas is None:
        return {"state": "unknown", "gate_open": False,
                "detail": "OAS unavailable - buy gate closed for safety"}
    if oas >= c["systemic_min"] or rapid:
        state = "systemic"
    elif oas >= c["elevated_max"]:
        state = "stress"       # elevated_max .. systemic_min
    elif oas >= c["benign_max"]:
        state = "elevated"
    else:
        state = "benign"
    return {
        "state": state,
        "oas_bps": _round(oas, 0),
        "oas_change_bps": _round(chg, 0) if chg is not None else None,
        "rapidly_widening": rapid,
        "gate_open": state != "systemic",
        "detail": _CREDIT_DETAIL[state],
    }


_CREDIT_DETAIL = {
    "benign": "OAS < benign_max: credit calm. Buy-side gate OPEN.",
    "elevated": "OAS elevated but not stressed. Gate open, mild caution.",
    "stress": "OAS in stress band. Gate open but reduce risk appetite; "
              "watch for the circuit breaker.",
    "systemic": "OAS systemic or rapidly widening. BUY GATE CLOSED (S4 defense).",
    "unknown": "OAS unavailable.",
}


# ---------------------------------------------------------------------------
# Circuit breaker (anti-falling-knife)
# ---------------------------------------------------------------------------

def _circuit_breaker(s: dict, credit: dict, cfg: dict) -> dict:
    cb = cfg["circuit_breaker"]
    if not cb.get("enabled"):
        return {"tripped": False, "detail": "disabled"}
    dd = s.get("drawdown")
    oas_rising = (s.get("oas_change") or 0) > 0
    deep = dd is not None and dd <= -abs(cb["drawdown_pct"])
    in_stress = credit.get("state") == "stress"
    tripped = bool(deep and in_stress and (oas_rising or not cb["require_oas_rising"]))
    return {
        "tripped": tripped,
        "drawdown_pct": _round(dd, 1) if dd is not None else None,
        "detail": (
            "Deep drawdown with widening credit stress: BUY signals downgraded "
            "to avoid a 2008-style falling knife."
            if tripped else "not tripped"
        ),
    }


# ---------------------------------------------------------------------------
# Scenario classification (gap-free, credit-gated)
# ---------------------------------------------------------------------------

def _classify(s: dict, credit: dict, breaker: dict, cfg: dict) -> dict:
    v = cfg["vix"]
    fg_cfg = cfg["fear_greed"]
    dd_cfg = cfg["drawdown"]
    vix = s.get("vix")
    fg = s.get("fg")
    dd = s.get("drawdown")

    # MASTER OVERRIDE: systemic credit => S4 regardless of VIX/F&G.
    if not credit["gate_open"]:
        return _scn("S4", "Systemic Risk", "DEFENSE - DO NOT BUY",
                    "Credit is systemic (OAS high or rapidly widening). De-leverage, "
                    "cut high-beta, hold cash, wait for spreads to stabilise.",
                    ["credit.systemic"])

    if vix is None or fg is None:
        return _scn("NA", "Insufficient data", "NO ACTION",
                    "VIX or Fear & Greed unavailable.", [])

    # S5 Overly optimistic (sell lean): SUSTAINED low VIX + extreme greed.
    if s.get("vix_calm_sustained") and fg > fg_cfg["greed_max"]:
        return _scn("S5", "Overly Optimistic", "REDUCE / TAKE PROFIT",
                    "Sustained low VIX with extreme greed: market not pricing risk. "
                    "Trim winners, sell covered calls, rebuild cash.",
                    ["vix.low_sustained", "fg.extreme_greed"])

    # S3 Extreme panic (strong buy) - gate open, confirmed extreme VIX, deep fear.
    if (s.get("vix_confirm_extreme") and fg <= fg_cfg["extreme_panic_fg_max"]):
        action = "STRONG BUY (CORE)"
        detail = ("Extreme panic with credit intact: contrarian buy core, "
                  "high-cash-flow assets; keep some cash, do not go all-in.")
        rs = ["vix.extreme_confirmed", "fg<=15", "credit.gate_open"]
        if breaker["tripped"]:
            action = "CAUTIOUS ADD ONLY"
            detail = ("Extreme panic BUT circuit breaker tripped (deep drawdown + "
                      "widening credit stress). Only small, cautious adds; the "
                      "2008 analog kept falling here.")
            rs.append("circuit_breaker")
        return _scn("S3", "Extreme Panic", action, detail, rs)

    # S2 Panic (scale-in) - confirmed tense VIX up to extreme, fear, real drawdown.
    if (s.get("vix_confirm_tense") and fg < fg_cfg["extreme_fear_max"]
            and dd is not None and dd <= -dd_cfg["panic_min"]):
        action = "SCALE-IN BUY"
        detail = ("Panic with stable credit: deploy cash in tranches. "
                  "(Covers VIX from tense_low up to extreme_low - no 30-35 gap.)")
        rs = ["vix.tense_confirmed", "fg<extreme_fear_max",
              f"drawdown<=-{dd_cfg['panic_min']}%", "credit.gate_open"]
        if breaker["tripped"]:
            action = "HOLD - BUY SUPPRESSED"
            detail = ("Panic but circuit breaker tripped: hold, do not add into a "
                      "deepening structural decline.")
            rs.append("circuit_breaker")
        return _scn("S2", "Panic", action, detail, rs)

    # S1 Normal pullback - mild VIX, mild fear, shallow drawdown.
    if (vix is not None and v["calm_high"] <= vix < v["tense_low"]
            and fg_cfg["extreme_fear_max"] <= fg <= fg_cfg["fear_max"]
            and dd is not None and -dd_cfg["normal_high"] <= dd <= -dd_cfg["normal_low"]):
        return _scn("S1", "Normal Pullback", "HOLD / CONTINUE DCA",
                    "Ordinary pullback, credit and cross-assets normal. No panic; "
                    "keep regular dollar-cost averaging.",
                    ["vix.calm_band", "fg.fear_band", "drawdown.shallow"])

    # DEFAULT - fills every gap the original matrix left (neutral, recovery, etc.)
    return _scn("NEUTRAL", "No Clear Signal", "HOLD / MONITOR",
                "Conditions don't match any actionable scenario (e.g. neutral "
                "sentiment, recovery phase, or vol spike without a drawdown). "
                "Maintain current allocation and monitor.",
                _neutral_reasons(s, cfg))


def _neutral_reasons(s: dict, cfg: dict) -> List[str]:
    out = []
    if s.get("vix") is not None:
        out.append(f"vix={_round(s['vix'],1)}")
    if s.get("fg") is not None:
        out.append(f"fg={_round(s['fg'],1)}")
    if s.get("drawdown") is not None:
        out.append(f"drawdown={_round(s['drawdown'],1)}%")
    return out


def _scn(code, name, action, detail, reasons) -> dict:
    return {"code": code, "name": name, "action": action,
            "detail": detail, "reasons": reasons}


# ---------------------------------------------------------------------------
# Cash deployment tranche (only when gate open & a buy scenario fired)
# ---------------------------------------------------------------------------

def _deployment(s: dict, credit: dict, scenario: dict, breaker: dict,
                cfg: dict) -> dict:
    d = cfg["deployment"]
    v = cfg["vix"]
    if not credit["gate_open"] or scenario["code"] not in ("S2", "S3"):
        return {"recommended_pct": 0, "tranche": None,
                "detail": "No deployment: buy gate closed or non-buy scenario."}
    if breaker["tripped"]:
        return {"recommended_pct": 0, "tranche": None,
                "detail": "No deployment: circuit breaker tripped."}
    vix = s.get("vix")
    if vix is None:
        return {"recommended_pct": 0, "tranche": None,
                "detail": "No deployment: VIX unavailable."}
    peak_drop = s.get("vix_peak_drop") or 0
    falling_from_peak = peak_drop >= d["peak_drop_pts"]
    if vix >= v["blowoff"] or falling_from_peak:
        return {"recommended_pct": d["tranche_3_pct"], "tranche": 3,
                "detail": "VIX blew off (>=blowoff) or is falling from peak: "
                          "deploy final tranche."}
    if vix >= v["panic_low"]:
        return {"recommended_pct": d["tranche_2_pct"], "tranche": 2,
                "detail": "VIX in panic band: deploy second tranche."}
    return {"recommended_pct": d["tranche_1_pct"], "tranche": 1,
            "detail": "VIX in tense band: deploy first tranche."}


def _fg_label(value: Optional[float], cfg: dict) -> str:
    if value is None:
        return "unknown"
    if value <= cfg["extreme_fear_max"]:
        return "Extreme Fear"
    if value <= cfg["fear_max"]:
        return "Fear"
    if value <= cfg["neutral_max"]:
        return "Neutral"
    if value <= cfg["greed_max"]:
        return "Greed"
    return "Extreme Greed"
