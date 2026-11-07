# Strategy Engine & Risk Management Reference

The **Strategy Engine** provides the infrastructure for designing, evaluating, and managing systematic swing trading strategies on the National Stock Exchange of India (NSE).

---

## 1. Core Architecture

The system decouples **Screeners** (stock selection) from **Strategies** (trade execution and capital preservation):

```
+----------------------------------------------------------------+
|                     1. Market Data Panel                       |
|   MySQL daily_prices -> Adjusted Prices -> TechnicalCalculator |
+-------------------------------+--------------------------------+
                                |
                                v
+----------------------------------------------------------------+
|                     2. Screener Execution                      |
|       Screener.run(df) / evaluate_screener_history(df)         |
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

### Example Strategy JSON

```json
{
  "name": "Swing Volume ATR Trailing",
  "version": "1.0",
  "description": "Trend alignment with volume surge entry, 2.0x ATR stop, and dynamic trailing stop.",
  "status": "candidate",
  "universe": "NIFTY 500",
  "entry": {
    "screener_key": "swing_volume",
    "inline_rules": ["rsi_14 < 70"],
    "timing": "next_open"
  },
  "exit": {
    "stop_loss_type": "atr",
    "stop_loss_value": 2.0,
    "target_type": "none",
    "trailing_stop_type": "atr",
    "trailing_stop_value": 2.0,
    "breakeven": true,
    "move_to_breakeven_at_r": 1.5,
    "max_holding_days": 20
  },
  "sizing": {
    "method": "risk_percent",
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
