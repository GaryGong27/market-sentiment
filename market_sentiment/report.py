"""Render the engine result to the terminal and to a JSON-serialisable dict."""

from __future__ import annotations

from typing import Dict, List


_ACTION_MARK = {
    "STRONG BUY (CORE)": "++",
    "SCALE-IN BUY": "+",
    "HOLD / CONTINUE DCA": "=",
    "HOLD / MONITOR": "=",
    "REDUCE / TAKE PROFIT": "-",
    "DEFENSE - DO NOT BUY": "x",
    "CAUTIOUS ADD ONLY": "~",
    "HOLD - BUY SUPPRESSED": "~",
    "NO ACTION": "?",
}


def _f(x, suffix="", nd=1):
    if x is None:
        return "n/a"
    if isinstance(x, float):
        return f"{x:.{nd}f}{suffix}"
    return f"{x}{suffix}"


def to_terminal(result: dict, warnings: List[str]) -> str:
    s = result["signals"]
    scn = result["scenario"]
    credit = result["credit"]
    regime = result["regime"]
    cb = result["circuit_breaker"]
    dep = result["deployment"]
    L: List[str] = []
    bar = "=" * 64

    L.append(bar)
    L.append("  MARKET SENTIMENT  -  credit-gated, regime-aware")
    L.append(bar)

    mark = _ACTION_MARK.get(scn["action"], " ")
    L.append(f"  VERDICT: [{scn['code']}] {scn['name']}")
    L.append(f"  ACTION : [{mark}] {scn['action']}")
    L.append(f"           {scn['detail']}")
    L.append("")

    L.append(f"  Regime : {regime.get('label', regime.get('name'))}")
    L.append(f"           {regime.get('detail','')}")
    L.append("")

    L.append("  METRICS")
    L.append(f"    VIX            {_f(s.get('vix'))}  (50d MA {_f(s.get('vix_ma'))})"
             f"  confirm: tense={s.get('vix_confirm_tense')} "
             f"panic={s.get('vix_confirm_panic')} extreme={s.get('vix_confirm_extreme')}")
    fg = s.get("fg")
    comp = s.get("fg_components", {})
    L.append(f"    Fear & Greed   {_f(fg)}  ({s.get('fg_label','n/a')})"
             f"  [{len(comp)} components: {', '.join(comp) or 'none'}]")
    L.append(f"    Credit OAS     {_f(credit.get('oas_bps'),' bps',0)}  "
             f"(IG {_f(s.get('ig_oas'),' bps',0)}, 30d chg "
             f"{_f(credit.get('oas_change_bps'),' bps',0)})  -> {credit['state'].upper()}")
    L.append(f"    Index DD       {_f(s.get('drawdown'),'%')}  (from {s.get('asof',{}).get('SPY','?')} trailing high)")
    L.append(f"    Breadth        RSP/SPY {_f(s.get('rsp_spy_change'),'%')}  "
             f"IWM/SPY {_f(s.get('iwm_spy_change'),'%')}  "
             f"narrow={s.get('breadth_narrow')}")
    L.append(f"    Cross-asset    10Y {_f(s.get('y10'),'%',2)} (chg {_f(s.get('y10_change_bps'),'bps',0)})  "
             f"real {_f(s.get('real_yield'),'%',2)}  DXY {_f(s.get('dxy_change'),'%')}  "
             f"Gold {_f(s.get('gold_change'),'%')}")
    L.append("")

    L.append("  GATES")
    L.append(f"    Credit gate    {'OPEN' if credit['gate_open'] else 'CLOSED'}  - {credit['detail']}")
    L.append(f"    Circuit breaker {'TRIPPED' if cb['tripped'] else 'ok'}  - {cb['detail']}")
    if dep.get("tranche"):
        L.append(f"    Deployment     tranche {dep['tranche']}: deploy {dep['recommended_pct']}% of cash"
                 f"  - {dep['detail']}")
    else:
        L.append(f"    Deployment     none  - {dep['detail']}")
    L.append("")

    if warnings:
        L.append("  DATA WARNINGS")
        for w in warnings:
            L.append(f"    ! {w}")
        L.append("")

    L.append("  Reasons: " + ", ".join(scn.get("reasons", [])))
    L.append(bar)
    L.append("  NOT INVESTMENT ADVICE. Signals are heuristic and can be wrong; "
             "see caveats in README/skill.")
    L.append(bar)
    return "\n".join(L)


def to_json(result: dict, warnings: List[str], generated_at: str) -> dict:
    return {
        "generated_at": generated_at,
        "verdict": {
            "code": result["scenario"]["code"],
            "name": result["scenario"]["name"],
            "action": result["scenario"]["action"],
            "detail": result["scenario"]["detail"],
            "reasons": result["scenario"]["reasons"],
        },
        "regime": result["regime"],
        "credit": result["credit"],
        "circuit_breaker": result["circuit_breaker"],
        "deployment": result["deployment"],
        "signals": result["signals"],
        "warnings": warnings,
        "disclaimer": "Not investment advice. Heuristic signals, may be wrong.",
    }
