---
name: market-sentiment
description: "Pull live market data (VIX, Fear & Greed, breadth, credit spreads, cross-asset) and emit a credit-gated, regime-aware buy/hold/sell verdict with cash-deployment tranches. Use when asked for market sentiment, whether to buy the dip, risk-on/off, or a read on fear/greed."
argument-hint: "[--json-only] [--no-cache]"
triggers:
  - "market sentiment"
  - "buy the dip"
  - "fear and greed"
  - "risk on or risk off"
  - "should i buy"
  - "market read"
---

# Market Sentiment

Runs a 5-metric sentiment engine and prints a single verdict (one of S1-S5 or
NEUTRAL) with an action and, when a buy is authorized, a cash-deployment tranche.

## What it does

Implements a **corrected** version of a retail capital-allocation framework.
Corrections (verified by historical fact-check) baked into the engine:

- **Credit is the master gate.** High-yield OAS (FRED `BAMLH0A0HYM2`,
  duration-neutral), not bond-ETF price, decides systemic risk and overrides
  every buy signal. This resolves the original S3/S4 overlap and avoids the
  2022 false positive (HYG/JNK fell on rates, not credit).
- **VIX 30-35 gap closed.** S2 scale-in spans VIX 25 up to 35; S3 strong-buy
  takes over at 35. No uncovered band.
- **Regime detection** via the real yield (`DFII10`): rising real yields flag
  the inflation regime where stock-bond correlation is positive and the
  "wait for the yield to peak" rule buys late.
- **Circuit breaker** suppresses buys in deep drawdowns with widening credit
  stress (the 2008 falling-knife zone).
- **Confirmation windows**: VIX triggers need consecutive closes; the S5 sell
  lean needs sustained complacency. Defeats one-day vol spikes (Feb-2018).

## Scenarios

| Code | Name | Action |
|------|------|--------|
| S1 | Normal pullback | Hold / continue DCA |
| S2 | Panic | Scale-in buy (tranches) |
| S3 | Extreme panic | Strong buy (core) |
| S4 | Systemic risk | Defense - do not buy |
| S5 | Overly optimistic | Reduce / take profit |
| NEUTRAL | No clear signal | Hold / monitor |

## How to run

Invoke the runner script (do not inline logic here):

```
bash ~/.claude/skills/market-sentiment/run.sh
```

Pass through flags, e.g. machine-readable output:

```
bash ~/.claude/skills/market-sentiment/run.sh --json-only
bash ~/.claude/skills/market-sentiment/run.sh --no-cache
```

The runner ensures dependencies (`requests`, `PyYAML`) are present, then runs
`~/cody/market-sentiment/main.py`. JSON reports are written to
`~/cody/market-sentiment/reports/sentiment-YYYY-MM-DD.json`.

## Tuning

All thresholds (VIX bands, OAS basis-point gates, drawdown %, confirmation
days, deployment tranche sizes) live in `~/cody/market-sentiment/config.yaml`.
Edit numbers there; no code change needed.

## Data sources (keyless)

Yahoo chart API (VIX, SPY, RSP, IWM, HYG, JNK, GLD, IEF, DXY) and FRED CSV
(HY/IG OAS, 10Y nominal & real yield). No API keys required.

## Caveats (read before acting)

- **Not investment advice.** Heuristic signals; they can and do fail.
- Fear & Greed is **reconstructed from 4 of CNN's 7 components** (momentum,
  volatility, safe-haven, junk demand). NYSE highs/lows and put/call are off by
  default — no reliable keyless source. The number approximates CNN's, not
  matches it.
- Breadth uses **coarse ETF ratios** (RSP/SPY, IWM/SPY), not constituent-level
  advance-decline. It flags concentration, not precise timing.
- Signals are **end-of-day**; intraday VIX is not used.
- The engine reduces a complex market into one verdict; always read the
  underlying metrics, not just the headline action.
