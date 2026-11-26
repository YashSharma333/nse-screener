# Strategy Engine & Risk Management Reference

The **Strategy Engine** provides the infrastructure for designing, evaluating, and managing systematic swing trading strategies on the National Stock Exchange of India (NSE).

---

## 1. Core Architecture

A **Strategy** is a complete trading plan. Its entry is a **condition** (today a list of rules, later a condition tree, see [condition_engine.md](condition_engine.md)); the rest of the plan is trade execution and capital preservation. The prebuilt screeners were removed in CL-24B.

```
+----------------------------------------------------------------+
|                     1. Market Data Panel                       |
|   MySQL daily_prices -> Adjusted Prices -> add_all_indicators  |
+-------------------------------+--------------------------------+
                                |
                                v
+----------------------------------------------------------------+
|                     2. Entry Rule Evaluation                   |
|        evaluate_rules_history(entry.inline_rules, df)         |
|             Generates Candidate Trade Signals (t)              |
+-------------------------------+--------------------------------+
                                |
                                v
+----------------------------------------------------------------+
|                  3. Strategy Engine Evaluation                 |
|   StrategySpec (JSON) -> Entry / Exit / Sizing / Cost Model    |
|   Orders fill next open (t+1) with slippage                    |
+-------------------------------+--------------------------------+
                                |
                                v
+----------------------------------------------------------------+
|             4. Risk Containment & Sizing (risk.py)             |
|   - Mandatory initial stop (ATR, %, support)                   |
|   - Position size bounded by 1% risk budget & capital cap      |
|   - Dynamic trailing stop ratchet                              |
|   - Breakeven adjustments                                      |
+----------------------------------------------------------------+
```

---

## 2. Strategy Specification (`StrategySpec`)

Strategies are expressed as validated Pydantic v2 JSON models with a defined lifecycle:

- `draft`: Experimental design in development.
- `candidate`: Backtested and undergoing forward verification.
- `approved`: Validated for live execution.
- `retired`: Deprecated historical model.

### Entry rules

`entry.inline_rules` is a list of rule expressions; the entry signal is true on a day when ALL of them are true. Rules read indicator columns (`close`, `sma_50`, `rsi_14`, `vol_multiple` ...) with comparisons, `+ - * /`, `and`, `or` and `not`. Function calls and anything else are refused (`src/conditions/rules.py`, no `eval`). The old `entry.screener_key` is rejected with a message. A stop measured in ATR needs `atr_14`; `signals` stops with an error if it is missing instead of guessing a number.

### Example Strategy JSON

```json
{
  "name": "Swing Volume ATR Trailing",
  "version": "1.0",
  "description": "Trend alignment with volume surge entry, 2.0x ATR stop, and dynamic trailing stop.",
  "status": "candidate",
  "entry": {
    "inline_rules": [
      "ema_20 > sma_50",
      "sma_50 > sma_200",
      "close > sma_50",
      "close > sma_200",
      "close >= 0.85 * high_252d_prev",
      "vol_multiple > 1.5",
      "volume_sma_20 > 250000",
      "rsi_14 < 70"
    ]
  },
  "exit": {
    "stop_loss_type": "atr",
    "stop_loss_value": 2.0,
    "target_type": "none",
    "trailing_stop_type": "atr",
    "trailing_stop_value": 2.0,
    "move_to_breakeven_at_r": 1.5,
    "max_holding_days": 20
  },
  "sizing": {
    "risk_percent": 1.0,
    "max_positions": 5,
    "max_portfolio_heat_percent": 5.0
  },
  "costs": {
    "slippage_bps": 5.0,
    "dp_charge_per_scrip_day": 0.0,
    "exchange_turnover_rate": 0.0000307
  }
}
```

---

## 3. Risk Primitives & Formulas (`src/strategies/risk.py`)

All risk containment functions are pure, deterministic mathematical functions:

| Function | Formula / Principle |
|---|---|
| `round_to_tick(price)` | Rounds to nearest exchange tick (₹0.05) using `ROUND_HALF_UP`. |
| `risk_per_share(entry, stop)` | $R_{share} = \text{entry} - \text{stop}$ (must be $> 0$). |
| `initial_stop(...)` | ATR: $\text{entry} - (\text{mult} \times \text{ATR})$; %: $\text{entry} \times (1 - \text{pct}/100)$. |
| `target_price(...)` | R-Multiple: $\text{entry} + (\text{R\_mult} \times R_{share})$. |
| `position_size(...)` | $\lfloor (\text{equity} \times \text{risk\_pct}) / R_{share} \rfloor$, bounded by max capital cap. |
| `trail_next(...)` | Dynamic stop ratchet: $\max(\text{current\_trail}, \text{new\_trail})$. Stop never loosens. |
| `r_multiple(...)` | $(\text{exit} - \text{entry}) / R_{share}$. |
| `portfolio_heat(...)` | $\sum ((\text{entry}_i - \text{stop}_i) \times \text{shares}_i) / \text{equity} \times 100$. |

---

## 4. Statutory Cost Schedule (NSE Cash Delivery)

Trading costs are strictly calculated using verified official fee schedules (`config/costs.json`):

| Cost Item | Rate | Source / Circular |
|---|---|---|
| **Exchange Transaction Fee + IPFT** | 0.00307% (₹307 per crore total: 306.99 fee + 0.01 IPFT) | NSE Circular `NSE/FA/73061` (issued 27 Feb 2026, effective 1 Mar 2026) |
| **STT (Securities Transaction Tax)** | 0.1% buy, 0.1% sell | Finance Act 2004, Finance (No. 2) Act 2024 |
| **Stamp Duty** | 0.015% buy only (₹0 sell) | Indian Stamp Act, 1899 |
| **SEBI Turnover Fee** | ₹10 per crore (0.0001%) | SEBI (Stock Brokers and Sub-brokers) Regulations |
| **GST** | 18% on (Brokerage + Exchange + SEBI) | CGST & IGST Acts |
| **DP Charge** | Broker-specific; charged once per scrip per sell day | Brokerage contract (e.g. ₹0 or ₹15.00) |
| **Slippage** | 5.0 bps default | Execution model |
| **STCG (Tax)** | 20% on realized net gain | Section 111A, Income-tax Act (user parameter) |

---

## 5. Strategy CLI Usage

Manage and inspect strategies directly from the terminal:

```bash
# List all registered library strategies
python -m src.strategies.cli list

# Validate a strategy JSON specification
python -m src.strategies.cli validate strategies/library/swing_volume_atr_trail.json

# Evaluate today's trade signals with position sizing for ₹500,000 equity
python -m src.strategies.cli signals strategies/library/swing_volume_atr_trail.json --equity 500000
```
