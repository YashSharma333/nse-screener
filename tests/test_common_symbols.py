"""normalise_symbol: trim spaces and upper-case, nothing else."""

from src.common.symbols import normalise_symbol


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
