"""parse_nse_date: every format the old private parsers accepted, plus the two failure styles."""

from datetime import date, datetime

import pandas as pd
import pytest

from src.common.dates import NSE_DATE_FORMATS, is_blank_date, parse_nse_date


@pytest.mark.parametrize(
    "text",
    [
        "19-SEP-2019",
        "19-Sep-2019",
        "19-September-2019",
        "19-09-2019",
        "2019-09-19",
        " 19-sep-2019 ",
    ],
)
def test_default_formats(text):
    assert parse_nse_date(text) == date(2019, 9, 19)


def test_objects_pass_through_as_plain_dates():
    assert parse_nse_date(date(2024, 1, 5)) == date(2024, 1, 5)
    assert parse_nse_date(pd.Timestamp("2024-01-05")) == date(2024, 1, 5)
    result = parse_nse_date(datetime(2024, 1, 5, 9, 30))
    assert type(result) is date and result == date(2024, 1, 5)


@pytest.mark.parametrize("blank", [None, float("nan"), pd.NaT, "", "  ", "-", "NA", "null"])
def test_blank_values_give_none_and_count_as_blank(blank):
    assert is_blank_date(blank)
    assert parse_nse_date(blank) is None


def test_unreadable_text_gives_none_when_not_strict():
    assert parse_nse_date("not a date") is None
    assert not is_blank_date("not a date")


def test_custom_formats_mcap_style():
    formats = ("%d %b %Y", "%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y")
    assert parse_nse_date("01 FEB 2024", formats) == date(2024, 2, 1)
    assert parse_nse_date("01/02/2024", formats) == date(2024, 2, 1)


def test_month_first_format():
    assert parse_nse_date("04-06-2023", ("%m-%d-%Y",)) == date(2023, 4, 6)
    assert parse_nse_date("2023-04-06", ("%m-%d-%Y",)) is None


def test_pandas_fallback_only_when_dayfirst_is_given():
    assert parse_nse_date("06 Apr 23") is None
    assert parse_nse_date("06 Apr 23", dayfirst=True) == date(2023, 4, 6)


def test_strict_raises_the_requested_error_with_label_and_hint():
    class MyError(Exception):
        pass

    with pytest.raises(MyError, match=r"exDate: 'oops' is not a %d-%b-%Y date\. Changed\."):
        parse_nse_date(
            "oops", ("%d-%b-%Y",), strict=True, error=MyError, label="exDate", hint="Changed."
        )


def test_strict_default_error_is_value_error_and_blank_is_an_error_too():
    with pytest.raises(ValueError, match="is not a"):
        parse_nse_date("", strict=True)
    assert parse_nse_date("2024-01-05", strict=True) == date(2024, 1, 5)


def test_default_formats_are_the_documented_four():
    assert NSE_DATE_FORMATS == ("%d-%b-%Y", "%d-%B-%Y", "%d-%m-%Y", "%Y-%m-%d")
