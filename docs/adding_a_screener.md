# Adding a Systematic Screener

This guide walks you through building, testing, and registering a new systematic stock screener in **NSE Screener**.

---

## Screener vs Strategy: Vocabulary Policy

- **Screener:** A systematic list of `True`/`False` conditions evaluated across equities to select candidates (e.g. Minervini, Swing Volume, Breakout 52W).
- **Strategy:** A complete trade management plan encompassing entry timing, mandatory stop-loss, profit target, position sizing, holding period limits, and cost modeling.
- *Never refer to a screener as a "strategy" in code, docs, or UI.*

---

## Approach 1: Python-Coded Screener (Recommended for Complex Setups)

### 1. Create the Files

Create the screener module and its companion test by hand (copy a small existing screener in `src/screeners/catalog.py` as a starting point):

- `src/screeners/my_breakout.py`
- `tests/test_screener_my_breakout.py`

### 2. Implement the Screening Conditions

A screener lists the indicators it needs as **specs** built from its parameters, and its rules compare plain columns. The resolver (`src/computation/indicators/resolver.py`) adds any missing column, so any number the user types (SMA 75, RSI period 7) works. Build conditions from the helpers in `src/screeners/catalog.py`:

```python
from typing import Any, ClassVar, Literal

import pandas as pd

from src.computation.indicators.resolver import IndicatorSpec, ind
from src.screeners.base import Screener, register_screener
from src.screeners.catalog import _cond_above_sma, _cond_volume_surge


@register_screener()
class MyBreakoutScreener(Screener):
    key = "my_breakout"
    name = "My Volume Breakout"
    description = "High volume breakout above the moving average"
    category = "breakout"
    version = "1.0"
    status: Literal["core", "experimental"] = "experimental"
    # Parameters are plain numbers; the sidebar lets the user change every one of them.
    default_params: ClassVar[dict[str, Any]] = {"sma_length": 50, "volume_mult": 2.0}

    def specs(self) -> dict[str, IndicatorSpec]:
        """The indicators this screener reads, by short name, with the numbers from the params."""
        return {
            "sma": ind("sma", length=self.params["sma_length"]),
            # yesterday's 20-day average volume (days_ago=1), the Chartink-style surge rule
            "volume_avg_prev": ind("sma", source="volume", length=20, days_ago=1),
        }

    def rules(self, df: pd.DataFrame, p: dict) -> dict[str, pd.Series]:
        return {
            "above_sma": _cond_above_sma(df, self.col("sma")),
            "volume_surge": _cond_volume_surge(
                df, multiplier=p["volume_mult"], vol_sma_col=self.col("volume_avg_prev")
            ),
        }
```

`self.col("sma")` is the column name of the spec (for example `sma_close_50`). Columns that the resolver cannot make yet (benchmark and rank columns such as `rs_63d`, crossover flags) go in `required_columns = ("rs_63d",)` and are read from the cache. Use `src/computation/indicators/resolver.py` (`describe_indicators()`) to see every indicator and its parameters.

> [!IMPORTANT]
> **No Look-Ahead or Runtime Shifts:** Never call `.shift()`, `.rolling()`, or `.pct_change()` inside screener code. Ask for the indicator through a spec (`ind(..., days_ago=n)` looks back inside each stock). Missing columns must raise `MissingIndicatorError`.

Run a screener on a table of each stock's history with `screener.screen(history)` (it explains the newest day), or on prepared rows with `screener.explain(screener.prepare(df))`.

### 3. Register It

`@register_screener()` (in `src/screeners/base.py`) registers the class when its module is imported. Add the import to `src/screeners/__init__.py`:

```python
import src.screeners.my_breakout  # noqa: F401
```

### 4. Verify Against Screener Contract

Run the contract test suite:

```bash
pytest tests/test_screener_contract.py -k my_breakout
```

The contract suite automatically validates:
- Unique screener key and valid metadata
- Aligned boolean Series for all conditions (no NaNs)
- Correct `.explain(df)` columns (including `near_miss` and `failed_conditions`)
- AST static check verifying 0 forbidden look-ahead operations
- Point-in-time truncation invariance

---

## Approach 2: Declarative JSON Screener (No Python Required)

You can also define custom screeners without writing Python code by adding a JSON specification in `screeners/custom/`:

Create `screeners/custom/my_tight_consolidation.json`:

```json
{
  "key": "custom_tight_consolidation",
  "name": "Tight Consolidation",
  "category": "pattern",
  "description": "Equities in uptrend consolidating tightly with dry volume",
  "version": "1.0",
  "status": "experimental",
  "rules": [
    "close > sma_50",
    "close > sma_200",
    "volume < volume_sma_20 * 0.8"
  ],
  "evidence_columns": [
    "close",
    "sma_50",
    "sma_200",
    "volume",
    "volume_sma_20"
  ]
}
```

The system automatically parses expressions with a secure AST parser and registers the screener on startup.
