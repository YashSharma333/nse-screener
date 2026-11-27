"""
NSE corporate-action subject parser
===================================
Turns the free-text ``subject`` of an NSE corporate action, for example
``"Bonus 1:1/Face Value Split (Sub-Division) - From Rs 10/- Per Share To Re 1/- Per Share"``,
into a list of structured parts, each with an exact *price factor* when that is safe.

Why this file exists: NSE's own Bhavcopy PREV_CLOSE is not adjusted for splits and bonuses,
and guessing the ratio from price jumps gets some of them wrong (issue #48). The official
corporate-actions text states the exact ratio, so we read it.

How to read the price factor
----------------------------
``price_factor`` is the number you multiply every price **before** the ex-date by, so the
old prices line up with the new ones. Examples:

* split, face value Rs 10 to Rs 5:  5 / 10 = 0.5      (price halves)
* bonus 1:1 (1 new share for 1 held):  1 / (1 + 1) = 0.5
* bonus 1:2 (1 new share for 2 held):  2 / (1 + 2) = 0.6667
* consolidation, face value Re 1 to Rs 10:  10 / 1 = 10  (price rises tenfold)

When one day has several actions the factors are multiplied (split 10 to 2 and bonus 1:1
on the same day: 0.2 * 0.5 = 0.1). The multiplying is done later, by the code that reads
the table, so every single action stays visible.

Safety rule: anything ambiguous gets ``price_factor = None`` and status ``needs_review``.
It is never applied automatically.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

# Action types that change the share price in a way we can compute exactly.
PRICE_ACTION_TYPES: tuple[str, ...] = ("SPLIT", "BONUS", "CONSOLIDATION")

# Parse statuses. "exact": price_factor is set. "needs_review": might move the price but we
# cannot compute it safely. "info": does not change the price in a way we adjust for.
STATUS_EXACT = "exact"
STATUS_REVIEW = "needs_review"
STATUS_INFO = "info"

# Types that can change the price but need a person (or more data) to decide the factor.
_REVIEW_TYPES = ("RIGHTS", "DEMERGER", "MERGER", "ARRANGEMENT", "CAPITAL_REDUCTION")

_NUMBER = r"(\d+(?:\.\d+)?)"
# "Rs", "Rs.", "Re", "Re." in front of an amount, all optional (NSE spells it many ways).
_RUPEE = r"(?:rs\.?|re\.?)?\s*"

# "bonus 1:1", "bonus 1: 2" and "bonus- 1:2": A new shares for every B held.
_BONUS_RE = re.compile(r"bonus[\s\-]*" + _NUMBER + r"\s*:\s*" + _NUMBER)
# "from rs 10/- per share to re 1/- per share" (also "from rs 10 to rs 5").
_FROM_TO_RE = re.compile(
    r"from\s*" + _RUPEE + _NUMBER + r"\s*/?-?\s*(?:per share)?\s*to\s*" + _RUPEE + _NUMBER
)
# "rights 1:4", "rights issue 30:37@ ..." and "rights - 1:5": A new shares for every B held.
_RIGHTS_RE = re.compile(r"rights(?:\s*issue)?[\s:\-]*" + _NUMBER + r"\s*:\s*" + _NUMBER)
# Any "A:B" ratio, used to refuse a line that offers two different things.
_RATIO_RE = re.compile(_NUMBER + r"\s*:\s*" + _NUMBER)
# "@ Premium Rs 66/-", "Prem Rs 740", "Prm Rs 102/-", "Premium Of Rs 2.50 Per Share".
_PREMIUM_RE = re.compile(r"(?:premium|prem|prm)\b[\sa-z]*?(?:rs\.?|re\.?)\s*" + _NUMBER)
# Rights of something that is not a plain equity offer: no simple issue price to use.
_COMPLEX_RIGHTS_RE = re.compile(r"partly|convertible|debenture|warrant|preference")
# Bonus of something that is not the ordinary equity share does not move the share price.
_NON_EQUITY_BONUS_RE = re.compile(r"ncrps|debenture|preference|redeemable|\bncd\b")

# Order matters: the first keyword found names the action when no split or bonus matched.
_OTHER_TYPE_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("RIGHTS", r"rights"),
    ("DEMERGER", r"demerg|de-merg"),
    ("MERGER", r"merger|amalgam"),
    ("ARRANGEMENT", r"arrang|arang|acquisition"),
    ("CAPITAL_REDUCTION", r"reduction"),
    ("BUYBACK", r"buy ?back"),
    ("DIVIDEND", r"dividend|distribution|interest|redemption"),
    ("MEETING", r"general meeting|\bagm\b|\begm\b|book closure|board meeting"),
)


@dataclass(frozen=True)
class ActionPart:
    """One action found inside an NSE subject line."""

    action_type: str
    part_no: int
    parse_status: str
    price_factor: float | None = None
    old_face_value: float | None = None
    new_face_value: float | None = None
    bonus_new_shares: float | None = None
    bonus_held_shares: float | None = None
    rights_new: float | None = None
    rights_held: float | None = None


def normalise_subject(subject: str) -> str:
    """Lower-case the subject and collapse all whitespace (NSE pads many subjects)."""
    return " ".join(str(subject).lower().split())


@dataclass(frozen=True)
class RightsTerms:
    """What a rights issue offers: ``new_shares`` for every ``held_shares`` at ``issue_price``."""

    new_shares: float
    held_shares: float
    issue_price: float


def parse_rights_terms(subject: str, face_value: float | None) -> RightsTerms | None:
    """Read the offer of a rights issue from its NSE subject, or None when it is not clear.

    The issue price is the face value plus the premium NSE prints (``Premium Rs 0`` means
    issued at face value). We never guess: no premium, no face value, two offers in one line
    (fully and partly paid) or convertible debentures all give None, so the action stays in
    the review report instead of getting a made-up factor.
    """
    text = normalise_subject(subject)
    if "rights" not in text or face_value is None or _COMPLEX_RIGHTS_RE.search(text):
        return None
    ratios, premiums = _RATIO_RE.findall(text), _PREMIUM_RE.findall(text)
    if len(ratios) != 1 or len(premiums) != 1:
        return None
    new_shares, held_shares = float(ratios[0][0]), float(ratios[0][1])
    if new_shares <= 0 or held_shares <= 0:
        return None
    return RightsTerms(new_shares, held_shares, float(face_value) + float(premiums[0]))


def rights_factor(cum_price: float, terms: RightsTerms) -> float | None:
    """NSE's rights adjustment factor, or None when it cannot lower the price.

    NSE (equity derivatives, "Adjustments in case of Corporate Actions"): with P the close on
    the last cum-date, S the issue price, A new shares per B held,
    ``E = (P - S) * A / (A + B)`` and ``factor = (P - E) / P``. Earlier prices are multiplied
    by it. If S is not below P the offer gives no benefit, and a non-positive P is bad data.
    """
    if cum_price <= 0 or terms.issue_price >= cum_price:
        return None
    benefit = (cum_price - terms.issue_price) * terms.new_shares
    benefit /= terms.new_shares + terms.held_shares
    return round((cum_price - benefit) / cum_price, 8)


def subject_hash(subject: str) -> str:
    """Short stable fingerprint of a subject, used as part of the table's unique key.

    Why a hash: the subject is long free text, which makes a poor MySQL index column.
    """
    return hashlib.md5(normalise_subject(subject).encode("utf-8")).hexdigest()


def _bonus_parts(text: str, first_part_no: int) -> list[ActionPart]:
    """Find every ``bonus A:B`` in the text (A new shares for every B held)."""
    parts = []
    for match in _BONUS_RE.finditer(text):
        new_shares, held_shares = float(match.group(1)), float(match.group(2))
        factor = None
        status = STATUS_REVIEW
        if new_shares > 0 and held_shares > 0:
            factor = round(held_shares / (new_shares + held_shares), 8)
            status = STATUS_EXACT
        parts.append(
            ActionPart(
                action_type="BONUS",
                part_no=first_part_no + len(parts),
                parse_status=status,
                price_factor=factor,
                bonus_new_shares=new_shares,
                bonus_held_shares=held_shares,
            )
        )
    return parts


def _face_value_part(text: str, action_type: str, part_no: int) -> ActionPart:
    """Build a SPLIT or CONSOLIDATION part from ``from Rs X to Rs Y`` wording."""
    match = _FROM_TO_RE.search(text)
    if match is None:
        return ActionPart(action_type, part_no, STATUS_REVIEW)

    old_value, new_value = float(match.group(1)), float(match.group(2))
    # A split must shrink the face value and a consolidation must grow it. The opposite
    # means we misread the sentence, so leave it for a person.
    direction_ok = new_value < old_value if action_type == "SPLIT" else new_value > old_value
    if old_value <= 0 or new_value <= 0 or not direction_ok:
        return ActionPart(
            action_type,
            part_no,
            STATUS_REVIEW,
            old_face_value=old_value,
            new_face_value=new_value,
        )
    return ActionPart(
        action_type,
        part_no,
        STATUS_EXACT,
        price_factor=round(new_value / old_value, 8),
        old_face_value=old_value,
        new_face_value=new_value,
    )


def _other_part(text: str, part_no: int) -> ActionPart:
    """Name an action that is not a split, bonus or consolidation."""
    for action_type, pattern in _OTHER_TYPE_KEYWORDS:
        if not re.search(pattern, text):
            continue
        if action_type == "RIGHTS":
            match = _RIGHTS_RE.search(text)
            new_shares = float(match.group(1)) if match else None
            held_shares = float(match.group(2)) if match else None
            return ActionPart(
                action_type,
                part_no,
                STATUS_REVIEW,
                rights_new=new_shares,
                rights_held=held_shares,
            )
        status = STATUS_REVIEW if action_type in _REVIEW_TYPES else STATUS_INFO
        return ActionPart(action_type, part_no, status)
    return ActionPart("OTHER", part_no, STATUS_INFO)


def parse_subject(subject: str) -> list[ActionPart]:
    """Split one NSE subject line into its actions, with exact price factors when safe.

    A subject can hold several actions (``Bonus 1:1/Face Value Split ...``), so the result
    is a list. It always has at least one part.
    """
    text = normalise_subject(subject)

    # A bonus of debentures or preference shares ("Scheme Of Arrangement - Bonus NCRPS 1:1")
    # looks like a bonus but does not touch the equity share price.
    if "bonus" in text and _NON_EQUITY_BONUS_RE.search(text):
        return [ActionPart("ARRANGEMENT", 0, STATUS_REVIEW)]

    parts: list[ActionPart] = []
    if "bonus" in text:
        found = _bonus_parts(text, len(parts))
        # "bonus" with no readable ratio is still a bonus; a person must look at it.
        parts.extend(found or [ActionPart("BONUS", len(parts), STATUS_REVIEW)])

    if re.search(r"split|sub-?division", text):
        parts.append(_face_value_part(text, "SPLIT", len(parts)))
    elif "consolidation" in text:
        # "Capital Reduction ... / Consolidation ..." mixes two actions whose net effect
        # is unclear from the text, so only a plain consolidation is read.
        if "reduction" in text:
            parts.append(ActionPart("CONSOLIDATION", len(parts), STATUS_REVIEW))
        else:
            parts.append(_face_value_part(text, "CONSOLIDATION", len(parts)))

    if not parts:
        parts.append(_other_part(text, 0))
    return parts


def unparsed_price_share(parts: list[ActionPart]) -> float:
    """Share of split, bonus and consolidation parts that have no exact factor (0 to 1).

    This is the "canary" for NSE changing its wording: it is about 0.003 on real data.
    """
    price_parts = [part for part in parts if part.action_type in PRICE_ACTION_TYPES]
    if not price_parts:
        return 0.0
    unparsed = [part for part in price_parts if part.parse_status != STATUS_EXACT]
    return len(unparsed) / len(price_parts)
