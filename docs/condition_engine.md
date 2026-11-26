# Condition engine (planned, task CL-24C)

A **condition** is a saved tree of parts that is true or false per stock per day. The user composes it
like a Chartink scan: prices, indicators with custom numbers, index prices, math, comparisons and groups.
There are no prebuilt screeners (removed in CL-24B).

**Status: not built yet.** Today `src/conditions/rules.py` holds the safe rule parser that strategy
entries use (`entry.inline_rules`, see [strategy_engine.md](strategy_engine.md)). CL-24C builds the tree
evaluator in `src/conditions/` and replaces the rule text with condition trees.

## Planned nodes

| Node | Meaning |
|---|---|
| number | a constant |
| field | open, high, low, close, volume, market_cap, optionally N days ago |
| index | index close (NIFTY 50/100/500, MIDCAP 150, SMALLCAP 250), optionally N days ago |
| indicator | an indicator with custom numbers (for example SMA of close, 50), optionally N days ago |
| window | max, min, sum or count over N days of any part |
| math | + - * / between two parts |
| func | abs, sqrt, log, floor, ceil, round and similar |
| compare | > >= < <= = !=, crossed above, crossed below |
| group | all, any (at least 1), any 1 (exactly 1), any 2 (at least 2) |

## Rules that must hold

- Every look-back (shift, rolling, count, cross) runs inside each symbol.
- A value on day `t` uses data up to the close of `t` only.
- Not enough data gives false, never an error and never a silent default.
- No `eval` or `exec`.
