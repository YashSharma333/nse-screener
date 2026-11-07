# Adding a Systematic Screener

This guide walks you through building, testing, and registering a new systematic stock screener in **NSE Screener**.

---

## Screener vs Strategy: Vocabulary Policy

- **Screener:** A systematic list of `True`/`False` conditions evaluated across equities to select candidates (e.g. Minervini, Swing Volume, Breakout 52W).
- **Strategy:** A complete trade management plan encompassing entry timing, mandatory stop-loss, profit target, position sizing, holding period limits, and cost modeling.
- *Never refer to a screener as a "strategy" in code, docs, or UI.*

---

## Approach 1: Python-Coded Screener (Recommended for Complex Setups)

### 1. Scaffold the New Screener

Use the built-in generator CLI to create the screener module and companion unit test:

```bash
python -m src.screeners.scaffold \
  --key my_breakout \
  --name "My Volume Breakout" \
  --category breakout \
  --description "High volume breakout above 50-day moving average" \
  --status experimental
```

This generates:
- `src/screeners/my_breakout.py`
- `tests/test_screener_my_breakout.py`

### 2. Implement the Screening Conditions

Open `src/screeners/my_breakout.py` and organize your conditions strictly by nature:

```python
from dataclasses import dataclass
from typing import Literal
import pandas as pd
from src.screeners.base import Screener
from src.screeners.conditions.price import cond_above_sma
from src.screeners.conditions.volume import cond_volume_surge
from src.screeners.registry import register_screener


@dataclass(frozen=True)
class MyBreakoutParams:
    sma_window: int = 50
    volume_surge_mult: float = 2.0


@register_screener(
    key="my_breakout",
    name="My Volume Breakout",
    category="breakout",
    description="High volume breakout above 50-day moving average",
    version="1.0",
    status="experimental",
)
class MyBreakoutScreener(Screener):
    key = "my_breakout"
    name = "My Volume Breakout"
    category = "breakout"
    version = "1.0"
    status: Literal["core", "experimental"] = "experimental"

    evidence_columns: tuple[str, ...] = ("close", "sma_50", "volume", "volume_sma_20")
    required_columns: tuple[str, ...] = ("close", "sma_50", "volume", "volume_sma_20")
    params_cls = MyBreakoutParams

    def price_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        return {
            "above_50_sma": cond_above_sma(df, sma_col="sma_50"),
        }

    def volume_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        params: MyBreakoutParams = self.params
        return {
            "volume_surge": cond_volume_surge(df, multiplier=params.volume_surge_mult),
        }
```

> [!IMPORTANT]
> **No Look-Ahead or Runtime Shifts:** All technical indicators must be precomputed by `TechnicalCalculator`. Never call `.shift()`, `.rolling()`, or `.pct_change()` inside screener condition helpers. Missing columns must raise `MissingIndicatorError`.

### 3. Register in `src/screeners/__init__.py`

Add an import in `src/screeners/__init__.py`:

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
- Logical `AND` equivalence of `.run(df)`
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
