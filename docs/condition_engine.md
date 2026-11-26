# Condition engine

A **condition** is a saved tree of parts that is true or false per stock per day. The user composes it
like a Chartink scan: prices, indicators with custom numbers, index prices, math, comparisons and
groups. There are no prebuilt screeners (removed in CL-24B). The code is in `src/conditions/`.

```python
from src.conditions import parse_condition, evaluate, load_condition, save_condition

node = parse_condition(tree_as_a_dict)  # checks the tree; ConditionError says where it is wrong
answer = evaluate(node, price_history)  # True/False for every stock-day (a pandas Series)
saved = load_condition("my_scan.json")  # name, description and the checked tree
```

`price_history` has `symbol`, `date` and the columns the tree reads (open, high, low, close, volume,
market_cap), many days per stock, because averages and "N days ago" need the earlier rows. The answer
on day `t` uses data up to the close of `t` only. An index part also needs `benchmarks`
(`index_code`, `date`, `close`, the `benchmark_prices` shape) as a third argument.

## The parts

Every part is an object with a `"type"`. A part gives either a **number** per stock-day or a
**true/false** answer.

| type | gives | fields |
|---|---|---|
| `number` | number | `value` |
| `field` | number | `name` (open, high, low, close, volume, market_cap), `days_ago` (default 0) |
| `index` | number | `index` ("NIFTY 500" ...), `name` (open, high, low, close; default close), `days_ago` |
| `indicator` | number | `name` (sma, ema, wma, rsi, macd, bollinger_bands, atr, true_range, volatility, percent_change), `params` (numbers such as `{"length": 50}`), optional `source` (any number part), `days_ago` |
| `window` | number | `stat` (max, min, sum, mean, count), `length`, `source` (any part; a true/false part for count), `days_ago` |
| `math` | number | `op` (`+ - * /`), `left`, `right` |
| `func` | number | `name` (abs, sqrt, log, log10, floor, ceil, round, square), `arg` |
| `compare` | true/false | `op` (`> >= < <= = !=`, `crossed_above`, `crossed_below`), `left`, `right` |
| `group` | true/false | `mode`, `children` |

Defaults are in the tool: `days_ago` is 0, an indicator's numbers keep the defaults of its function
(`sma` is length 20 on the close). A saved file holds every field, so it says everything.

### Group modes

| mode | true when |
|---|---|
| `all` | every child is true |
| `any` | at least 1 child is true |
| `any_1` | exactly 1 child is true |
| `any_2` | at least 2 children are true |

## Rules that always hold

- **Every look-back runs inside each stock.** `days_ago`, windows, averages and crosses never see
  another stock's rows. `days_ago` on a window is applied AFTER the window
  (`1 day ago Max(252, WMA(close, 10))` is yesterday's finished maximum).
- **Unknown, not a default.** A number is unknown (NaN) when there is not enough history or the
  maths has no answer (divide by 0, log of 0). A compare with an unknown side is unknown, a count
  window with an unknown row inside is unknown, a group answers only when its unknown children
  cannot change the answer (so `any` is true when one child is true even if another is unknown).
  The final answer turns every unknown into False.
- `count` takes a true/false part: `Count(20, WMA(volume, 10) >= SMA(volume, 50)) <= 6`.
- A part inside another part must fit: `compare` and `math` need number parts, `group` needs
  true/false parts. A mistake is reported with its place, for example
  `at group.children[1].compare.left: ...`.
- Index prices: the benchmark table has the close only today. An index `open`, `high` or `low`
  stops with a clear message until the index OHLC download exists. "N days ago" of an index counts
  rows of the index's own calendar.
- No `eval` or `exec`: a condition is data, never code.

Not covered: drawn patterns, fundamentals (EPS, ROCE ...), intraday timeframes.

## Example: relative strength against NIFTY 500

The 252-day return of the stock is higher than the 252-day return of the index:

```json
{"type":"compare","op":">",
 "left":{"type":"math","op":"*","right":{"type":"number","value":100},
  "left":{"type":"math","op":"/",
   "left":{"type":"math","op":"-","left":{"type":"field","name":"close"},"right":{"type":"field","name":"close","days_ago":252}},
   "right":{"type":"field","name":"close","days_ago":252}}},
 "right":{"type":"math","op":"*","right":{"type":"number","value":100},
  "left":{"type":"math","op":"/",
   "left":{"type":"math","op":"-","left":{"type":"index","index":"NIFTY 500"},"right":{"type":"index","index":"NIFTY 500","days_ago":252}},
   "right":{"type":"index","index":"NIFTY 500","days_ago":252}}}}
```

## Saved files and strategies

A saved scan is `{"name", "version": 1, "description", "condition": <tree>}`
(`save_condition` / `load_condition`). A strategy (`strategies/library/*.json`) holds its entry as
`entry.condition` and optional `exit.exit_rules` (a list of trees); see
[strategy_engine.md](strategy_engine.md). The three library strategies are the old prebuilt
screeners written as trees, and give the same entry signals on the golden dates.

## Cost

Evaluating a strategy over the whole cache (1.47 million rows) takes about 4 to 8 seconds. Parts
are not shared between branches of a tree yet; CL-25 (the 16-column cache) is where that is tuned.
