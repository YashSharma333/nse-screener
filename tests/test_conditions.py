"""Tests of the condition engine (CL-24C): every node kind, every group mode, JSON, no look-ahead.

A condition is a JSON tree. The tests write the trees as plain dicts, exactly what a saved scan
holds, and check the true/false answer per stock per day against numbers worked out by hand.
"""

from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd
import pytest

from src.conditions import (
    ConditionError,
    evaluate,
    evaluate_number,
    load_condition,
    parse_condition,
    save_condition,
)

DAYS = 270


# ---------------------------------------------------------------------------
# Small builders so the trees in the tests stay short
# ---------------------------------------------------------------------------
def num(value: float) -> dict:
    return {"type": "number", "value": value}


def field(name: str = "close", days_ago: int = 0) -> dict:
    return {"type": "field", "name": name, "days_ago": days_ago}


def index(name: str = "close", days_ago: int = 0, code: str = "NIFTY 500") -> dict:
    return {"type": "index", "index": code, "name": name, "days_ago": days_ago}


def math_(op: str, left: dict, right: dict) -> dict:
    return {"type": "math", "op": op, "left": left, "right": right}


def cmp(op: str, left: dict, right: dict) -> dict:
    return {"type": "compare", "op": op, "left": left, "right": right}


def indicator(name: str, source: dict | None = None, days_ago: int = 0, **params) -> dict:
    node = {"type": "indicator", "name": name, "params": params, "days_ago": days_ago}
    if source is not None:
        node["source"] = source
    return node


def window(stat: str, length: int, source: dict, days_ago: int = 0) -> dict:
    return {
        "type": "window",
        "stat": stat,
        "length": length,
        "source": source,
        "days_ago": days_ago,
    }


def group(mode: str, *children: dict) -> dict:
    return {"type": "group", "mode": mode, "children": list(children)}


def make_table(**per_symbol: list[float]) -> pd.DataFrame:
    """One block of rows per stock (close given, open/high/low = close, volume 1000)."""
    frames = []
    for symbol, closes in per_symbol.items():
        dates = pd.bdate_range("2024-01-01", periods=len(closes)).date
        close = pd.Series(closes, dtype=float)
        frames.append(
            pd.DataFrame(
                {
                    "symbol": symbol,
                    "date": dates,
                    "open": close,
                    "high": close,
                    "low": close,
                    "close": close,
                    "volume": 1000.0,
                }
            )
        )
    return pd.concat(frames, ignore_index=True)


def run(tree: dict, table: pd.DataFrame, benchmarks: pd.DataFrame | None = None) -> pd.Series:
    return evaluate(parse_condition(tree), table, benchmarks)


def number(tree: dict, table: pd.DataFrame, benchmarks: pd.DataFrame | None = None) -> pd.Series:
    return evaluate_number(parse_condition(tree), table, benchmarks)


def as_list(series: pd.Series) -> list:
    return [None if pd.isna(v) else v for v in series.tolist()]


# ---------------------------------------------------------------------------
# number and field
# ---------------------------------------------------------------------------
def test_number_is_the_same_value_on_every_row() -> None:
    table = make_table(AAA=[1, 2, 3])
    assert number(num(5), table).tolist() == [5.0, 5.0, 5.0]


def test_field_reads_the_column_and_days_ago_shifts_inside_each_stock() -> None:
    table = make_table(AAA=[10, 11, 12], BBB=[20, 21, 22])
    assert as_list(number(field("close", 1), table)) == [None, 10, 11, None, 20, 21]


def test_field_with_unknown_name_is_refused() -> None:
    with pytest.raises(ConditionError, match="name"):
        parse_condition(field("vwap"))


def test_field_with_missing_column_in_the_table_is_a_clear_error() -> None:
    table = make_table(AAA=[1, 2, 3]).drop(columns=["volume"])
    with pytest.raises(ConditionError, match="volume"):
        number(field("volume"), table)


# ---------------------------------------------------------------------------
# index
# ---------------------------------------------------------------------------
def benchmarks_for(table: pd.DataFrame, values: list[float], code: str = "NIFTY 500"):
    dates = sorted(table["date"].unique())
    return pd.DataFrame({"index_code": code, "date": dates, "close": values})


def test_index_close_is_matched_by_date_and_days_ago_shifts_the_index_rows() -> None:
    table = make_table(AAA=[1, 2, 3, 4])
    bench = benchmarks_for(table, [100, 110, 120, 130])
    assert number(index(), table, bench).tolist() == [100, 110, 120, 130]
    assert as_list(number(index(days_ago=2), table, bench)) == [None, None, 100, 110]


def test_index_value_on_a_day_the_index_has_no_row_is_unknown_so_false() -> None:
    table = make_table(AAA=[1, 2, 3])
    bench = benchmarks_for(table, [100, 110, 120]).iloc[[0, 2]]  # the middle day is missing
    result = run(cmp(">", index(), num(0)), table, bench)
    assert result.tolist() == [True, False, True]


def test_index_open_high_low_need_a_benchmark_table_that_has_them() -> None:
    table = make_table(AAA=[1, 2, 3])
    bench = benchmarks_for(table, [100, 110, 120])  # close only, like benchmark_prices today
    with pytest.raises(ConditionError, match="open"):
        number(index("open"), table, bench)
    bench["open"] = [99.0, 109.0, 119.0]
    assert number(index("open"), table, bench).tolist() == [99, 109, 119]


def test_index_without_a_benchmark_table_or_with_an_unknown_index_is_a_clear_error() -> None:
    table = make_table(AAA=[1, 2, 3])
    with pytest.raises(ConditionError, match="benchmark"):
        number(index(), table, None)
    bench = benchmarks_for(table, [1, 2, 3])
    with pytest.raises(ConditionError, match="NIFTY 999"):
        number(index(code="NIFTY 999"), table, bench)


# ---------------------------------------------------------------------------
# indicator
# ---------------------------------------------------------------------------
def test_indicator_uses_the_default_source_and_custom_numbers() -> None:
    table = make_table(AAA=[1, 2, 3, 4, 5], BBB=[10, 10, 10, 10, 10])
    result = number(indicator("sma", length=3), table)
    assert as_list(result) == [None, None, 2, 3, 4, None, None, 10, 10, 10]


def test_indicator_source_can_be_any_part_and_days_ago_is_applied_after() -> None:
    table = make_table(AAA=[1, 2, 3, 4, 5, 6])
    table["volume"] = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0]
    result = number(indicator("sma", field("volume"), 1, length=2), table)
    # SMA(volume, 2) is [-, 15, 25, 35, 45, 55]; one day ago moves it down one row
    assert as_list(result) == [None, None, 15, 25, 35, 45]


def test_indicator_of_an_indicator_two_levels_deep() -> None:
    table = make_table(AAA=[1, 2, 3, 4, 5, 6])
    inner = indicator("sma", length=2)  # [-, 1.5, 2.5, 3.5, 4.5, 5.5]
    result = number(indicator("sma", inner, length=2), table)  # [-, -, 2, 3, 4, 5]
    assert as_list(result) == [None, None, 2, 3, 4, 5]


def test_indicator_source_with_math_inside() -> None:
    table = make_table(AAA=[1, 2, 3, 4])
    source = math_("*", field("close"), num(10))
    assert as_list(number(indicator("sma", source, length=2), table)) == [None, 15, 25, 35]


def test_indicator_with_a_wrong_name_or_number_is_refused_with_the_choices() -> None:
    with pytest.raises(ConditionError, match="smaa"):
        parse_condition(indicator("smaa", length=3))
    with pytest.raises(ConditionError, match="width"):
        parse_condition(indicator("sma", width=3))
    with pytest.raises(ConditionError, match="source"):
        parse_condition(indicator("atr", field("close")))  # atr reads high/low/close itself


def test_indicator_rolls_inside_each_stock() -> None:
    table = make_table(AAA=[1, 2, 3], BBB=[100, 200, 300])
    # BBB's first average must not see AAA's last rows
    assert as_list(number(indicator("sma", length=2), table)) == [None, 1.5, 2.5, None, 150, 250]


# ---------------------------------------------------------------------------
# window
# ---------------------------------------------------------------------------
def test_window_max_min_sum_mean() -> None:
    table = make_table(AAA=[1, 5, 3, 4])
    assert as_list(number(window("max", 2, field()), table)) == [None, 5, 5, 4]
    assert as_list(number(window("min", 2, field()), table)) == [None, 1, 3, 3]
    assert as_list(number(window("sum", 2, field()), table)) == [None, 6, 8, 7]
    assert as_list(number(window("mean", 2, field()), table)) == [None, 3, 4, 3.5]


def test_window_days_ago_shifts_after_the_window() -> None:
    table = make_table(AAA=[1, 5, 3, 4, 2])
    # Max over 2 rows is [-, 5, 5, 4, 4]; one day ago moves the finished result down one row
    assert as_list(number(window("max", 2, field(), days_ago=1), table)) == [None, None, 5, 5, 4]


def test_window_never_mixes_stocks() -> None:
    table = make_table(AAA=[100, 100], BBB=[1, 2])
    assert as_list(number(window("max", 2, field()), table)) == [None, 100, None, 2]


def test_window_count_of_a_true_false_part() -> None:
    table = make_table(AAA=[1, 5, 1, 5, 5])
    up = cmp(">", field(), num(3))  # [F, T, F, T, T]
    assert as_list(number(window("count", 3, up), table)) == [None, None, 1, 2, 2]


def test_window_count_is_unknown_until_every_row_of_the_window_is_known() -> None:
    # SMA(close, 3) needs 3 rows, so the comparison is unknown on rows 0 and 1; a count over
    # 2 rows is therefore unknown until row 3, and "count <= 5" must NOT be true before that.
    table = make_table(AAA=[1, 2, 3, 4, 5])
    known = cmp(">=", field(), indicator("sma", length=3))
    counted = cmp("<=", window("count", 2, known), num(5))
    assert run(counted, table).tolist() == [False, False, False, True, True]


def test_window_rules_about_what_the_source_must_be() -> None:
    with pytest.raises(ConditionError, match="count"):
        parse_condition(window("count", 3, field()))  # count needs a true/false part
    with pytest.raises(ConditionError, match="number"):
        parse_condition(window("max", 3, cmp(">", field(), num(1))))  # max needs numbers
    with pytest.raises(ConditionError, match="length"):
        parse_condition(window("max", 0, field()))


# ---------------------------------------------------------------------------
# math and func
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(("op", "expected"), [("+", 12.0), ("-", 8.0), ("*", 20.0), ("/", 5.0)])
def test_math_operations(op: str, expected: float) -> None:
    assert number(math_(op, num(10), num(2)), make_table(AAA=[1])).tolist() == [expected]


def test_division_by_zero_is_unknown_not_infinity() -> None:
    table = make_table(AAA=[1, 2])
    result = number(math_("/", field(), num(0)), table)
    assert result.isna().all()
    assert run(cmp(">", math_("/", field(), num(0)), num(5)), table).tolist() == [False, False]


@pytest.mark.parametrize(
    ("name", "value", "expected"),
    [
        ("abs", -2.5, 2.5),
        ("sqrt", 9.0, 3.0),
        ("log", math.e, 1.0),
        ("log10", 1000.0, 3.0),
        ("floor", 2.7, 2.0),
        ("ceil", 2.1, 3.0),
        ("round", 2.5, 2.0),
        ("round", 2.6, 3.0),
        ("square", 4.0, 16.0),
    ],
)
def test_functions(name: str, value: float, expected: float) -> None:
    tree = {"type": "func", "name": name, "arg": num(value)}
    assert number(tree, make_table(AAA=[1])).tolist() == [pytest.approx(expected)]


def test_function_outside_its_range_is_unknown() -> None:
    table = make_table(AAA=[1])
    for name, value in (("sqrt", -4.0), ("log", 0.0), ("log10", -1.0)):
        tree = {"type": "func", "name": name, "arg": num(value)}
        assert number(tree, table).isna().all(), name


def test_unknown_function_is_refused() -> None:
    with pytest.raises(ConditionError, match="name"):
        parse_condition({"type": "func", "name": "sin", "arg": num(1)})


# ---------------------------------------------------------------------------
# compare
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("op", "expected"),
    [
        (">", [False, False, True]),
        (">=", [False, True, True]),
        ("<", [True, False, False]),
        ("<=", [True, True, False]),
        ("=", [False, True, False]),
        ("!=", [True, False, True]),
    ],
)
def test_compare_operators(op: str, expected: list[bool]) -> None:
    table = make_table(AAA=[1, 2, 3])
    assert run(cmp(op, field(), num(2)), table).tolist() == expected


def test_compare_with_an_unknown_side_is_false_for_every_operator() -> None:
    table = make_table(AAA=[1, 2, 3])
    unknown = field("close", 1)  # NaN on the first row
    for op in (">", ">=", "<", "<=", "=", "!="):
        assert run(cmp(op, unknown, num(0)), table).tolist()[0] is False, op


def test_crossed_above_and_below_inside_each_stock() -> None:
    #           day:  0  1  2  3  4  5
    table = make_table(AAA=[1, 3, 5, 2, 1, 6], BBB=[9, 9, 9, 9, 9, 9])
    line = num(2.5)
    above = run(cmp("crossed_above", field(), line), table)
    below = run(cmp("crossed_below", field(), line), table)
    assert above.tolist()[:6] == [False, True, False, False, False, True]
    assert below.tolist()[:6] == [False, False, False, True, False, False]
    # BBB sits above the line all the time: its first row has no yesterday, so no cross
    assert above.tolist()[6:] == [False] * 6


def test_crossed_above_does_not_use_the_previous_stocks_last_row() -> None:
    # AAA ends high, BBB starts high: BBB's first row must not look like a cross from AAA
    table = make_table(AAA=[1, 1, 1], BBB=[5, 5, 5])
    result = run(cmp("crossed_above", field(), num(2)), table)
    assert result.tolist() == [False] * 6


def test_compare_needs_numbers_on_both_sides() -> None:
    with pytest.raises(ConditionError, match="number"):
        parse_condition(cmp(">", cmp(">", field(), num(1)), num(0)))


# ---------------------------------------------------------------------------
# group
# ---------------------------------------------------------------------------
def test_group_modes_on_a_truth_table() -> None:
    table = make_table(AAA=[0, 1, 2, 3])
    t1, t2, t3 = (cmp(">=", field(), num(k)) for k in (1, 2, 3))  # true on rows >=1, >=2, >=3
    for mode, expected in {
        "all": [False, False, False, True],
        "any": [False, True, True, True],
        "any_1": [False, True, False, False],  # exactly one true
        "any_2": [False, False, True, True],  # at least two true
    }.items():
        assert run(group(mode, t1, t2, t3), table).tolist() == expected, mode


def test_group_with_one_unknown_child() -> None:
    table = make_table(AAA=[1, 2, 3])
    unknown = cmp(">", field("close", 5), num(0))  # unknown on every row
    true = cmp(">", field(), num(0))
    false = cmp("<", field(), num(0))
    # unknown counts as "could be either"; the answer is True/False only if it cannot change
    assert run(group("all", true, unknown), table).tolist() == [False] * 3  # undecided -> false
    assert run(group("all", false, unknown), table).tolist() == [False] * 3  # decided false
    assert run(group("any", true, unknown), table).tolist() == [True] * 3  # decided true
    assert run(group("any_1", true, unknown), table).tolist() == [False] * 3  # undecided
    assert run(group("any_1", true, false), table).tolist() == [True] * 3
    assert run(group("any_2", true, true, unknown), table).tolist() == [True] * 3


def test_group_needs_true_false_children_and_at_least_one() -> None:
    with pytest.raises(ConditionError, match="true/false"):
        parse_condition(group("all", field()))
    with pytest.raises(ConditionError, match="children"):
        parse_condition(group("all"))
    with pytest.raises(ConditionError, match="mode"):
        parse_condition(group("some", cmp(">", field(), num(1))))


def test_groups_can_be_nested() -> None:
    table = make_table(AAA=[1, 2, 3, 4])
    inner = group("any", cmp("<", field(), num(2)), cmp(">", field(), num(3)))  # rows 0 and 3
    outer = group("all", inner, cmp(">", field(), num(0)))
    assert run(outer, table).tolist() == [True, False, False, True]


# ---------------------------------------------------------------------------
# JSON: parse, save, load, readable errors
# ---------------------------------------------------------------------------
def test_save_and_load_round_trip(tmp_path) -> None:
    tree = group(
        "all",
        cmp(">", indicator("ema", length=20), indicator("sma", length=50)),
        cmp("<=", window("count", 20, cmp(">", field(), num(1))), num(6)),
    )
    node = parse_condition(tree)
    path = tmp_path / "scan.json"
    save_condition(path, "My scan", node, description="two parts")
    saved = json.loads(path.read_text())
    assert saved["name"] == "My scan"
    assert saved["version"] == 1
    assert saved["description"] == "two parts"
    loaded = load_condition(path)
    assert loaded.name == "My scan"
    assert loaded.condition == node
    assert loaded.condition.model_dump(mode="json")["type"] == "group"


def test_defaults_are_filled_so_a_saved_file_says_everything() -> None:
    node = parse_condition(field())
    assert node.model_dump(mode="json") == {"type": "field", "name": "close", "days_ago": 0}


def test_error_says_where_in_the_tree_the_mistake_is() -> None:
    tree = group("all", cmp(">", field(), num(1)), cmp(">", {"type": "numbr", "value": 1}, num(1)))
    with pytest.raises(ConditionError) as error:
        parse_condition(tree)
    message = str(error.value)
    assert "children" in message and "1" in message and "left" in message
    assert "numbr" in message


def test_extra_or_misspelled_fields_are_refused() -> None:
    with pytest.raises(ConditionError, match="daysago"):
        parse_condition({"type": "field", "name": "close", "daysago": 3})
    with pytest.raises(ConditionError, match="days_ago"):
        parse_condition(field("close", -1))


def test_evaluate_a_number_tree_as_a_condition_is_refused() -> None:
    with pytest.raises(ConditionError, match="true/false"):
        evaluate(parse_condition(field()), make_table(AAA=[1]))
    with pytest.raises(ConditionError, match="number"):
        evaluate_number(parse_condition(cmp(">", field(), num(1))), make_table(AAA=[1]))


# ---------------------------------------------------------------------------
# The example conditions, worked out by hand
# ---------------------------------------------------------------------------
def rs_vs_nifty_500_tree() -> dict:
    def pct(a: dict, a_ago: dict) -> dict:
        change = math_("/", math_("-", a, a_ago), a_ago)
        return math_("*", change, num(100))

    return cmp(">", pct(field(), field("close", 252)), pct(index(), index(days_ago=252)))


def test_example_relative_strength_against_nifty_500() -> None:
    # AAA grows 1% a day, BBB grows 0.1% a day, the index grows 0.3% a day
    days = range(DAYS)
    table = make_table(
        AAA=[100 * 1.01**i for i in days],
        BBB=[100 * 1.001**i for i in days],
    )
    bench = benchmarks_for(table.iloc[:DAYS], [1000 * 1.003**i for i in days])
    result = run(rs_vs_nifty_500_tree(), table, bench)
    aaa, bbb = result.iloc[:DAYS], result.iloc[DAYS:]
    assert not aaa.iloc[:252].any() and not bbb.iloc[:252].any()  # no 252-day history yet
    assert aaa.iloc[252:].all()  # 1.01**252 beats 1.003**252
    assert not bbb.iloc[252:].any()  # 1.001**252 does not


def test_example_one_day_ago_max_of_wma_times_point_eight() -> None:
    # Straight line close = 100 + i. WMA(close, 10) of a line is the close minus 3 (worked out:
    # the weights 1..10 over 55 sit 7 rows behind the 10th row, and the 10th row is 10 ahead).
    table = make_table(AAA=[100.0 + i for i in range(DAYS)])
    tree = math_(
        "*",
        window("max", 252, indicator("wma", length=10), days_ago=1),
        num(0.8),
    )
    result = number(tree, table)
    # WMA is valid from row 9; a 252-row window of it is complete at row 260; one day ago: 261
    assert result.iloc[:261].isna().all()
    for row in (261, 265, 269):
        wma_yesterday = (100.0 + row - 1) - 3
        assert result.iloc[row] == pytest.approx(0.8 * wma_yesterday)


def test_example_count_of_wma_volume_above_sma_volume_at_most_six() -> None:
    rng = np.random.default_rng(7)
    volume = rng.integers(100, 1000, size=120).astype(float)
    table = make_table(AAA=[10.0] * 120)
    table["volume"] = volume

    tree = cmp(
        "<=",
        window(
            "count",
            20,
            cmp(
                ">=",
                indicator("wma", field("volume"), length=10),
                indicator("sma", field("volume"), length=50),
            ),
        ),
        num(6),
    )
    result = run(tree, table)

    # the same thing worked out with plain loops, one row at a time
    def wma(end: int) -> float:
        pieces = volume[end - 9 : end + 1]
        return float(sum((k + 1) * v for k, v in enumerate(pieces)) / 55)

    def sma(end: int) -> float:
        return float(volume[end - 49 : end + 1].mean())

    expected = []
    for row in range(120):
        first_full_window = 49 + 19  # SMA(50) is known from row 49, 20 known rows from row 68
        if row < first_full_window:
            expected.append(False)
            continue
        count = sum(wma(r) >= sma(r) for r in range(row - 19, row + 1))
        expected.append(count <= 6)
    assert result.tolist() == expected
    assert any(expected) and not all(expected[68:])  # the example is not trivially one answer


# ---------------------------------------------------------------------------
# No look-ahead: a day's answer never changes when later days are removed
# ---------------------------------------------------------------------------
def test_answer_on_day_t_does_not_depend_on_later_days() -> None:
    rng = np.random.default_rng(11)
    rows = 320
    closes = {s: list(100 * np.cumprod(1 + rng.normal(0.001, 0.02, rows))) for s in ("AAA", "BBB")}
    table = make_table(**closes)
    table["volume"] = rng.integers(100, 1000, size=len(table)).astype(float)
    bench = benchmarks_for(
        table.iloc[:rows], list(1000 * np.cumprod(1 + rng.normal(0, 0.01, rows)))
    )

    tree = group(
        "any_2",
        rs_vs_nifty_500_tree(),
        cmp("crossed_above", field(), indicator("ema", length=20)),
        cmp(
            ">",
            field("volume"),
            math_("*", indicator("sma", field("volume"), 1, length=20), num(1.2)),
        ),
        cmp("<=", window("count", 20, cmp(">", field(), indicator("sma", length=10))), num(11)),
        cmp(">=", field(), math_("*", window("max", 60, field("high"), days_ago=1), num(0.9))),
    )
    full = run(tree, table, bench)

    cut_day = sorted(table["date"].unique())[299]
    kept = table[table["date"] <= cut_day].reset_index(drop=True)
    truncated = run(tree, kept, bench[bench["date"] <= cut_day])
    same = full[(table["date"] <= cut_day).to_numpy()].reset_index(drop=True)
    assert truncated.tolist() == same.tolist()
    assert full.any()  # the tree does select something, so the comparison means something


# ---------------------------------------------------------------------------
# Loading a saved file that is wrong
# ---------------------------------------------------------------------------
def test_loading_a_missing_or_broken_or_wrong_file_is_a_readable_error(tmp_path) -> None:
    with pytest.raises(ConditionError, match="cannot read"):
        load_condition(tmp_path / "nothing.json")

    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    with pytest.raises(ConditionError, match="cannot read"):
        load_condition(broken)

    no_tree = tmp_path / "no_tree.json"
    no_tree.write_text(json.dumps({"name": "x"}))
    with pytest.raises(ConditionError, match="condition"):
        load_condition(no_tree)

    future = tmp_path / "future.json"
    future.write_text(json.dumps({"version": 9, "condition": cmp(">", field(), num(1))}))
    with pytest.raises(ConditionError, match="version"):
        load_condition(future)

    bad_tree = tmp_path / "bad_tree.json"
    bad_tree.write_text(json.dumps({"condition": {"type": "numbr"}}))
    with pytest.raises(ConditionError, match=r"bad_tree\.json"):
        load_condition(bad_tree)


def test_a_file_without_a_name_uses_the_file_name(tmp_path) -> None:
    path = tmp_path / "my_scan.json"
    path.write_text(json.dumps({"condition": cmp(">", field(), num(1))}))
    assert load_condition(path).name == "my_scan"
