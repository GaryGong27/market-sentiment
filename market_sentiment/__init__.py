"""Market sentiment engine: multi-metric, credit-gated, regime-aware.

Implements a corrected version of a 5-metric retail capital-allocation
framework (VIX, Fear & Greed, breadth, credit, cross-asset). Corrections
applied vs the original: credit OAS is the master gate (not bond-ETF price),
the VIX 30-35 scenario gap is closed, every vague term is a numeric config
value, regime is detected via the real yield, and a circuit breaker guards
against 2008-style structural declines.
"""

__version__ = "1.0.0"
