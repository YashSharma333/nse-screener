"""normalise_symbol: trim spaces and upper-case, nothing else."""

import pandas as pd

from src.common.symbols import drop_funds, is_fund_isin, normalise_symbol


def test_trims_and_uppercases():
    assert normalise_symbol("  tcs ") == "TCS"


def test_keeps_punctuation_and_digits():
    assert normalise_symbol("m&m") == "M&M"
    assert normalise_symbol("bajaj-auto") == "BAJAJ-AUTO"
    assert normalise_symbol("20microns") == "20MICRONS"


def test_non_text_is_turned_into_text_first():
    assert normalise_symbol(123) == "123"


def test_empty_stays_empty():
    assert normalise_symbol("   ") == ""


def test_fund_isin_is_inf_and_company_shares_are_not():
    assert is_fund_isin("INF204KB14I2")  # a gold ETF
    assert is_fund_isin(" inf789f01xx0 ")  # spaces and case do not matter
    assert not is_fund_isin("INE009A01021")  # Infosys: INE is a company share
    assert not is_fund_isin(None)  # a missing ISIN is never a guess


def test_drop_funds_keeps_company_rows_in_order():
    frame = pd.DataFrame(
        {
            "symbol": ["TCS", "GOLDBEES", "INFY"],
            "isin": ["INE467B01029", "INF204KB14I2", "INE009A01021"],
        }
    )

    assert drop_funds(frame)["symbol"].tolist() == ["TCS", "INFY"]
    assert drop_funds(frame[["symbol"]]).equals(frame[["symbol"]])  # no ISIN column: unchanged
