"""Small helpers for tests that need official corporate actions and price rows in SQLite."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session

from src.db.models import CorporateAction, DailyPrice


def add_official_action(
    session: Session,
    symbol: str,
    ex_date: date,
    factor: float | None = 0.5,
    action_type: str = "SPLIT",
    parse_status: str = "exact",
    part_no: int = 0,
) -> None:
    """Insert one row of the official ``corporate_actions`` table (like the CA1 downloader)."""
    session.add(
        CorporateAction(
            symbol=symbol,
            ex_date=ex_date,
            series="EQ",
            action_type=action_type,
            part_no=part_no,
            subject=f"test {action_type}",
            subject_hash=f"{symbol}-{ex_date}-{action_type}-{part_no}",
            price_factor=factor,
            parse_status=parse_status,
        )
    )
    session.flush()


def add_rights_action(
    session: Session, symbol: str, ex_date: date, subject: str, face_value: float = 10.0
) -> None:
    """Insert one RIGHTS row as the downloader stores it: status needs_review, no factor."""
    session.add(
        CorporateAction(
            symbol=symbol,
            ex_date=ex_date,
            series="EQ",
            action_type="RIGHTS",
            part_no=0,
            subject=subject,
            subject_hash=f"{symbol}-{ex_date}-rights",
            face_value=face_value,
            parse_status="needs_review",
        )
    )
    session.flush()


def add_placeholder_action(session: Session) -> None:
    """Make the official table non-empty without any price effect (a dividend for no stock)."""
    add_official_action(
        session,
        "NOSUCHSTOCK",
        date(2000, 1, 1),
        factor=None,
        action_type="DIVIDEND",
        parse_status="info",
    )


def add_prices(
    session: Session, symbol: str, closes: list[float], start: date = date(2024, 1, 1)
) -> list[date]:
    """Insert one daily row per close (open = high = low = close). Returns the dates used."""
    days = [start + timedelta(days=i) for i in range(len(closes))]
    for i, (day, close) in enumerate(zip(days, closes, strict=True)):
        session.add(
            DailyPrice(
                symbol=symbol,
                date=day,
                open=close,
                high=close,
                low=close,
                close=close,
                prev_close=closes[max(i - 1, 0)],
                volume=1000,
            )
        )
    session.flush()
    return days
