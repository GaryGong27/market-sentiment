#!/usr/bin/env python3
"""Market sentiment CLI.

Pulls VIX, breadth, credit and cross-asset data from keyless public sources,
reconstructs a CNN-style Fear & Greed index from components, runs the corrected
credit-gated rule engine, prints a verdict and writes a JSON report.

Usage:
    python3 main.py [--config config.yaml] [--json-only] [--no-cache]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

import yaml

from market_sentiment import engine, fetchers, report
from market_sentiment.fetchers import FetchError

HERE = os.path.dirname(os.path.abspath(__file__))

# (logical name, kind, source id, scale, required?)
#   kind: "yahoo" | "fred" | "cboe"
SOURCES = [
    ("VIX",     "yahoo", "^VIX",     1.0,   True),
    ("SPY",     "yahoo", "SPY",      1.0,   True),
    ("RSP",     "yahoo", "RSP",      1.0,   False),
    ("IWM",     "yahoo", "IWM",      1.0,   False),
    ("HYG",     "yahoo", "HYG",      1.0,   False),
    ("JNK",     "yahoo", "JNK",      1.0,   False),
    ("GLD",     "yahoo", "GLD",      1.0,   False),
    ("IEF",     "yahoo", "IEF",      1.0,   False),
    ("DXY",     "yahoo", "DX-Y.NYB", 1.0,   False),
    ("HY_OAS",  "fred",  "BAMLH0A0HYM2", 100.0, True),   # percent -> bps
    ("IG_OAS",  "fred",  "BAMLC0A0CM",   100.0, False),  # percent -> bps
    ("DGS10",   "fred",  "DGS10",        1.0,   False),
    ("DFII10",  "fred",  "DFII10",       1.0,   False),
    # NOTE: NYSE highs/lows (F&G "strength") and CBOE put/call have no reliable
    # keyless feed (FRED HIGNYL/LOWNYL 404; CBOE CSV 403). Their F&G components
    # are off by default in config.yaml. Wire a source here + flip the flag on.
]


def load_config(path: str) -> dict:
    with open(path) as fh:
        return yaml.safe_load(fh)


def fetch_all(cfg: dict, no_cache: bool):
    rng = cfg["data"]["range"]
    timeout = cfg["data"]["request_timeout_s"]
    retries = cfg["data"]["retries"]
    cache_min = 0 if no_cache else cfg["data"]["cache_minutes"]
    data, warnings = {}, []
    for name, kind, sid, scale, required in SOURCES:
        try:
            if kind == "yahoo":
                data[name] = fetchers.yahoo(sid, rng, timeout, retries, cache_min)
            elif kind == "fred":
                data[name] = fetchers.fred(sid, rng, timeout, retries, cache_min, scale)
            elif kind == "cboe":
                data[name] = fetchers.cboe_putcall(timeout, retries, cache_min)
        except FetchError as exc:
            msg = f"{name} ({kind}:{sid or 'putcall'}) unavailable: {exc}"
            if required:
                raise FetchError(f"REQUIRED source failed - {msg}") from exc
            warnings.append(msg)
    return data, warnings


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Market sentiment engine")
    ap.add_argument("--config", default=os.path.join(HERE, "config.yaml"))
    ap.add_argument("--json-only", action="store_true",
                    help="print only the JSON report (no terminal summary)")
    ap.add_argument("--no-cache", action="store_true", help="bypass on-disk cache")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    try:
        data, warnings = fetch_all(cfg, args.no_cache)
    except FetchError as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return 2

    result = engine.evaluate(data, cfg)
    generated_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = report.to_json(result, warnings, generated_at)

    if cfg["output"].get("write_json"):
        rdir = os.path.join(HERE, cfg["output"]["reports_dir"])
        os.makedirs(rdir, exist_ok=True)
        day = generated_at[:10]
        path = os.path.join(rdir, f"sentiment-{day}.json")
        with open(path, "w") as fh:
            json.dump(payload, fh, indent=2)

    if args.json_only:
        print(json.dumps(payload, indent=2))
    else:
        print(report.to_terminal(result, warnings))
        if cfg["output"].get("write_json"):
            print(f"\n  JSON report: {path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
