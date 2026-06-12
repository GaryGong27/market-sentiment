#!/usr/bin/env python3
"""Deterministic tests for the corrected rule engine.

Run: python3 tests/test_engine.py   (exit 0 = all pass)

These target the corrections that distinguish this engine from the original
framework: credit master-override, the closed VIX 30-35 gap, the circuit
breaker, confirmation windows, and tranche logic. They call the engine's
classification helpers with hand-built signal dicts so the logic is exercised
without depending on live market data.
"""

import os
import sys

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from market_sentiment import engine  # noqa: E402

CFG = yaml.safe_load(open(os.path.join(os.path.dirname(__file__), "..", "config.yaml")))

_results = []


def check(name, got, want):
    ok = got == want
    _results.append((name, ok, got, want))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: got={got!r} want={want!r}")


def sig(vix=20.0, fg=50.0, dd=-1.0, tense=False, extreme=False, calm=False,
        peak_drop=0.0):
    return {
        "vix": vix, "fg": fg, "drawdown": dd,
        "vix_confirm_tense": tense, "vix_confirm_panic": False,
        "vix_confirm_extreme": extreme, "vix_calm_sustained": calm,
        "vix_peak_drop": peak_drop,
    }


def credit(state, gate):
    return {"state": state, "gate_open": gate, "oas_bps": 300,
            "oas_change_bps": 0, "rapidly_widening": False, "detail": ""}


def breaker(tripped=False):
    return {"tripped": tripped, "drawdown_pct": None, "detail": ""}


# --- credit master override ----------------------------------------------
print("credit master override")
scn = engine._classify(sig(vix=40, fg=5, extreme=True), credit("systemic", False),
                       breaker(), CFG)
check("systemic credit -> S4 even at VIX40/fg5", scn["code"], "S4")

# --- VIX 30-35 gap is closed (the headline correction) -------------------
print("VIX 30-35 gap closed")
scn = engine._classify(sig(vix=32, fg=20, dd=-8, tense=True, extreme=False),
                       credit("benign", True), breaker(), CFG)
check("VIX 32 (old gap) -> S2 scale-in", scn["code"], "S2")

# --- S3 extreme panic ----------------------------------------------------
print("S3 extreme panic")
scn = engine._classify(sig(vix=38, fg=10, dd=-15, tense=True, extreme=True),
                       credit("benign", True), breaker(), CFG)
check("VIX38/fg10 benign -> S3", scn["code"], "S3")
check("S3 action strong buy", scn["action"], "STRONG BUY (CORE)")

# --- S3 downgraded by circuit breaker ------------------------------------
print("S3 + circuit breaker")
scn = engine._classify(sig(vix=38, fg=10, dd=-25, tense=True, extreme=True),
                       credit("stress", True), breaker(tripped=True), CFG)
check("breaker downgrades S3 action", scn["action"], "CAUTIOUS ADD ONLY")
check("still tagged S3", scn["code"], "S3")

# --- S2 panic ------------------------------------------------------------
print("S2 panic")
scn = engine._classify(sig(vix=27, fg=20, dd=-8, tense=True),
                       credit("benign", True), breaker(), CFG)
check("VIX27/fg20/dd-8 -> S2", scn["code"], "S2")

# --- S1 normal pullback --------------------------------------------------
print("S1 normal pullback")
scn = engine._classify(sig(vix=20, fg=35, dd=-4), credit("benign", True),
                       breaker(), CFG)
check("VIX20/fg35/dd-4 -> S1", scn["code"], "S1")

# --- S5 overly optimistic ------------------------------------------------
print("S5 overly optimistic")
scn = engine._classify(sig(vix=13, fg=80, dd=-0.5, calm=True),
                       credit("benign", True), breaker(), CFG)
check("VIX13 sustained + fg80 -> S5", scn["code"], "S5")

# --- NEUTRAL default fills the gaps ---------------------------------------
print("NEUTRAL default")
scn = engine._classify(sig(vix=20, fg=50, dd=-1), credit("benign", True),
                       breaker(), CFG)
check("neutral conditions -> NEUTRAL", scn["code"], "NEUTRAL")

# --- credit state thresholds ---------------------------------------------
print("credit state")
check("oas 276 benign", engine._credit_state({"oas": 276, "oas_change": -8}, CFG)["state"], "benign")
check("oas 700 stress", engine._credit_state({"oas": 700, "oas_change": 0}, CFG)["state"], "stress")
check("oas 950 systemic", engine._credit_state({"oas": 950, "oas_change": 0}, CFG)["state"], "systemic")
check("oas 500 +200bps rapid -> systemic",
      engine._credit_state({"oas": 500, "oas_change": 200}, CFG)["state"], "systemic")
check("systemic closes gate",
      engine._credit_state({"oas": 950, "oas_change": 0}, CFG)["gate_open"], False)

# --- circuit breaker -----------------------------------------------------
print("circuit breaker")
check("deep dd + stress + rising -> tripped",
      engine._circuit_breaker({"drawdown": -25, "oas_change": 50}, credit("stress", True), CFG)["tripped"], True)
check("deep dd but benign -> not tripped",
      engine._circuit_breaker({"drawdown": -25, "oas_change": 50}, credit("benign", True), CFG)["tripped"], False)

# --- deployment tranches -------------------------------------------------
print("deployment")
dep = engine._deployment(sig(vix=27, tense=True), credit("benign", True),
                         {"code": "S2"}, breaker(), CFG)
check("VIX27 S2 -> tranche 1", dep["tranche"], 1)
dep = engine._deployment(sig(vix=42), credit("benign", True),
                         {"code": "S3"}, breaker(), CFG)
check("VIX42 -> tranche 3", dep["tranche"], 3)
dep = engine._deployment(sig(vix=36, peak_drop=8), credit("benign", True),
                         {"code": "S3"}, breaker(), CFG)
check("VIX falling from peak -> tranche 3", dep["tranche"], 3)
dep = engine._deployment(sig(vix=40), credit("systemic", False),
                         {"code": "S4"}, breaker(), CFG)
check("gate closed -> 0%", dep["recommended_pct"], 0)

# --- breadth: short + long trend + structural level-vs-high --------------
print("breadth")


def _series(vals):
    return [(f"d{i:04d}", float(v)) for i, v in enumerate(vals)]


# IWM held flat (ratio constant) so RSP drives every case in isolation.
_FLAT_SPY = _series([100.0] * 300)
_FLAT_IWM = _series([50.0] * 300)


def _breadth(rsp_vals):
    return engine._breadth(
        {"SPY": _FLAT_SPY, "IWM": _FLAT_IWM, "RSP": _series(rsp_vals)}, CFG)


# Broadening: RSP/SPY rising into its high -> not narrow on any check.
b = _breadth([100 + i * 0.1 for i in range(300)])
check("steady broadening -> not narrow", b["breadth_narrow"], False)

# Short-window narrowing: flat then a sharp 20d drop.
b = _breadth([100.0] * 280 + [97.0] * 20)
check("short-window drop -> narrow", b["breadth_narrow"], True)

# Structural narrowness hidden by a short bounce: ratio peaks at 120, decays to
# ~104, then bounces to 110 over the last 20d. Old short-only logic (20d > 0)
# would call this NOT narrow; the long window + level-vs-high catch it.
_struct = []
for i in range(300):
    if i < 50:
        v = 100.0
    elif i < 150:
        v = 100.0 + (i - 50) * 0.2          # ramp 100 -> 120
    elif i < 200:
        v = 120.0                            # plateau at the annual high
    elif i < 280:
        v = 120.0 - (i - 200) * 0.2          # decay 120 -> ~104
    else:
        v = 104.0 + (i - 279) * 0.3          # bounce -> 110
    _struct.append(v)
b = _breadth(_struct)
check("structural narrow despite 20d bounce -> narrow", b["breadth_narrow"], True)
check("  ...and the 20d trend really is positive (the trap)",
      b["rsp_spy_change"] > 0, True)
check("  ...flagged via long-window or level-vs-high, not short",
      any("126d" in r or "below" in r for r in b["breadth_narrow_reasons"]), True)


# --- summary -------------------------------------------------------------
fails = [r for r in _results if not r[1]]
print(f"\n{len(_results)-len(fails)}/{len(_results)} passed")
sys.exit(1 if fails else 0)
