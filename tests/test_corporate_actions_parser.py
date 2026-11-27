"""Tests for the NSE corporate-action subject parser (task CA1).

The subjects below are real NSE wordings seen in the 10-year feed (18 Nov 2026), including
the awkward ones: a hyphen after "Bonus", spaces inside "1: 2", a bonus of preference shares,
and a combined bonus plus split on one line.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from src.ingestion.corporate_actions_parser import (
    STATUS_EXACT,
    STATUS_INFO,
    STATUS_REVIEW,
    parse_rights_terms,
    parse_subject,
    rights_factor,
    subject_hash,
    unparsed_price_share,
)

FIXTURE = Path(__file__).parent / "fixtures" / "corporate_actions_sample.json"


def _factors(subject: str) -> list[tuple[str, float | None]]:
    return [(part.action_type, part.price_factor) for part in parse_subject(subject)]


@pytest.mark.parametrize(
    ("subject", "expected_factor"),
    [
        ("Bonus 1:1", 0.5),  # 1 new share for 1 held: the price halves
        ("Bonus 1: 2", 2 / 3),  # space after the colon (ONGC 2016)
        ("Bonus 3:1", 0.25),  # 3 new shares for 1 held (Balmer Lawrie 2016)
        ("Bonus- 1:2", 2 / 3),  # hyphen after "Bonus" (Ajanta Pharma 2022)
        ("  Bonus 2:5 ", 5 / 7),  # padding around the text
    ],
)
def test_bonus_factor_is_held_over_total(subject: str, expected_factor: float) -> None:
    ((kind, factor),) = _factors(subject)
    assert kind == "BONUS"
    assert math.isclose(factor, expected_factor, abs_tol=1e-8)


@pytest.mark.parametrize(
    ("subject", "expected_factor"),
    [
        ("Face Value Split (Sub-Division) - From Rs 10/- Per Share To Re 1/- Per Share", 0.1),
        ("Face Value Split (Sub-Division) - From Rs 10/- Per Share To Rs 5/- Per Share", 0.5),
        ("Face Value Split From Rs 10/- Per Share To Rs 2/- Per Share", 0.2),
        ("Face Value Split (Sub-Division) - From Rs.10 To Rs.2", 0.2),  # dots, no "/-"
        ("Face Value Split (Sub-Division) - From Rs 10 Per Share To Rs 2.50 Per Share", 0.25),
    ],
)
def test_split_factor_is_new_over_old_face_value(subject: str, expected_factor: float) -> None:
    ((kind, factor),) = _factors(subject)
    assert kind == "SPLIT"
    assert math.isclose(factor, expected_factor, abs_tol=1e-8)


def test_combined_bonus_and_split_gives_two_parts_in_order() -> None:
    parts = parse_subject(
        "Bonus 1:1/Face Value Split (Sub-Division) - From Rs 10/- Per Share To Re 1/- Per Share"
    )
    assert [(p.action_type, p.part_no, p.price_factor) for p in parts] == [
        ("BONUS", 0, 0.5),
        ("SPLIT", 1, 0.1),
    ]


def test_plain_consolidation_is_exact_and_greater_than_one() -> None:
    ((kind, factor),) = _factors(
        "Consolidation Of Equity Shares From Re 1 Per Share To Rs 10 Per Share"
    )
    assert (kind, factor) == ("CONSOLIDATION", 10.0)


def test_capital_reduction_with_consolidation_is_left_for_review() -> None:
    (part,) = parse_subject("Capital Reduction Rs 10 To Rs 3.30 / Consolidation Rs 3.30 To Rs.10")
    assert part.action_type == "CONSOLIDATION"
    assert part.parse_status == STATUS_REVIEW
    assert part.price_factor is None


def test_bonus_of_preference_shares_is_not_a_price_event() -> None:
    (part,) = parse_subject("Scheme Of Arrangement - Bonus Ncrps 1:10")
    assert part.action_type == "ARRANGEMENT"
    assert part.parse_status == STATUS_REVIEW
    assert part.price_factor is None


@pytest.mark.parametrize(
    "subject",
    [
        "Face Value Split (Sub-Division)",  # no old and new value
        "Face Value Split (Sub-Division) - From Rs 1 Per Share To Rs 10 Per Share",  # wrong way
        "Bonus issue of one share for every two held",  # words, not a ratio
        "Bonus 0:1",  # nonsense ratio
    ],
)
def test_unreadable_price_actions_get_no_factor(subject: str) -> None:
    parts = parse_subject(subject)
    assert all(part.price_factor is None for part in parts)
    assert all(part.parse_status == STATUS_REVIEW for part in parts)


@pytest.mark.parametrize(
    ("subject", "kind", "status"),
    [
        ("Interim Dividend - Rs 0.35 Per Share", "DIVIDEND", STATUS_INFO),
        ("Annual General Meeting/Dividend - Rs 5 Per Share", "DIVIDEND", STATUS_INFO),
        ("Annual General Meeting", "MEETING", STATUS_INFO),
        ("Buyback", "BUYBACK", STATUS_INFO),
        ("Scheme Of Demerger", "DEMERGER", STATUS_REVIEW),
        ("Rights 87:38 @ Premium Of Rs 2.50 Per Share", "RIGHTS", STATUS_REVIEW),
        ("Something NSE has never written before", "OTHER", STATUS_INFO),
    ],
)
def test_non_price_actions_are_named_but_never_get_a_factor(
    subject: str, kind: str, status: str
) -> None:
    (part,) = parse_subject(subject)
    assert (part.action_type, part.parse_status, part.price_factor) == (kind, status, None)


def test_rights_ratio_is_kept() -> None:
    (part,) = parse_subject("Rights 87:38 @ Premium Of Rs 2.50 Per Share")
    assert (part.rights_new, part.rights_held) == (87.0, 38.0)


def test_subject_hash_ignores_case_and_spacing_but_not_content() -> None:
    assert subject_hash("Bonus 1:1") == subject_hash("  bonus   1:1 ")
    assert subject_hash("Bonus 1:1") != subject_hash("Bonus 1:2")


def test_unparsed_share_counts_only_price_actions() -> None:
    parts = (
        parse_subject("Bonus 1:1")
        + parse_subject("Face Value Split (Sub-Division)")
        + parse_subject("Interim Dividend - Rs 1 Per Share")
    )
    assert unparsed_price_share(parts) == 0.5  # 1 of the 2 price actions is unreadable
    assert unparsed_price_share(parse_subject("Annual General Meeting")) == 0.0


def test_known_problem_cases_from_the_real_feed() -> None:
    """The cases our price-jump detector got wrong now come out exact (blueprint section 13)."""
    rows = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def day_factor(symbol: str, ex_date: str) -> float:
        total = 1.0
        for row in rows:
            if (row["symbol"], row["exDate"]) == (symbol, ex_date):
                for part in parse_subject(row["subject"]):
                    if part.parse_status == STATUS_EXACT:
                        total *= part.price_factor
        return total

    assert math.isclose(day_factor("COCHINSHIP", "10-Jan-2024"), 0.5)  # we had 0.6
    assert math.isclose(day_factor("AHCL", "24-Apr-2026"), 0.1)  # split 10 to 2 and bonus 1:1
    assert math.isclose(day_factor("HARDWYN", "05-Jun-2023"), 0.075)  # split 10 to 1, bonus 1:3
    assert math.isclose(day_factor("PREMEXPLN", "21-Jun-2024"), 0.2)  # a missed split
    assert math.isclose(day_factor("MARATHON", "05-Apr-2018"), 0.5)  # a missed split
    assert day_factor("IDEA", "18-Mar-2020") == 1.0  # the 2020 "event" never existed


# ---------------------------------------------------------------------------
# Rights issues: NSE's formula (derivatives adjustment page) needs the issue price and the
# last cum-date close, so the parser only reads the terms and a pure function does the maths.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("subject", "face_value", "expected"),
    [
        ("Rights 6:19 @ Premium Rs 66/-", 10.0, (6.0, 19.0, 76.0)),  # issue price = FV + premium
        ("Rights 1:1 @ Premium Rs 0/-", 2.0, (1.0, 1.0, 2.0)),  # premium 0 = issued at face value
        ("Rights 18:91 @ Prem Rs 740", 10.0, (18.0, 91.0, 750.0)),
        ("Rights 7:10 @ Prm Rs 102/-", 1.0, (7.0, 10.0, 103.0)),
        ("Rights 2:5 @ Premium Re 0.85/-", 1.0, (2.0, 5.0, 1.85)),
        ("Rights 87:38 @ Premium Of Rs 2.50 Per Share", 10.0, (87.0, 38.0, 12.5)),
        ("Rights Issue 30:37@ Premium Rs 53/-", 10.0, (30.0, 37.0, 63.0)),
    ],
)
def test_rights_terms_are_read_from_the_subject(subject, face_value, expected) -> None:
    terms = parse_rights_terms(subject, face_value)
    assert (terms.new_shares, terms.held_shares, terms.issue_price) == expected


@pytest.mark.parametrize(
    "subject",
    [
        "Rights 2:3",  # no price stated: we never guess an issue price
        "Rights - 1:5 Fully Paid Up Shares @ Premium Rs 10/- Per Share / 1:5 Partly Paid Up "
        "Shares @ Premium Rs 4/- Per Share",  # two offers in one line
        "Rights:14 Compulosry Convertible Debentures For Every 20 Equity Shares",
        "Annual General Meeting",
    ],
)
def test_unreadable_rights_terms_are_none(subject: str) -> None:
    assert parse_rights_terms(subject, 10.0) is None


def test_rights_issue_wording_keeps_the_ratio_on_the_part() -> None:
    (part,) = parse_subject("Rights Issue 30:37@ Premium Rs 53/-")
    assert (part.rights_new, part.rights_held) == (30.0, 37.0)


def test_rights_factor_follows_nse_formula() -> None:
    """P=100, issue price 60, 1 new share per 4 held: E = (100-60)*1/5 = 8, factor = 92/100."""
    terms = parse_rights_terms("Rights 1:4 @ Premium Rs 50/-", 10.0)
    assert rights_factor(100.0, terms) == pytest.approx(0.92)


def test_rights_factor_is_none_when_it_cannot_lower_the_price() -> None:
    """Issue price at or above the market price gives no benefit to adjust for; bad prices too."""
    terms = parse_rights_terms("Rights 1:4 @ Premium Rs 50/-", 10.0)
    assert rights_factor(60.0, terms) is None
    assert rights_factor(40.0, terms) is None
    assert rights_factor(0.0, terms) is None
