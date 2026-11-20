"""
SQLAlchemy ORM Models
=====================
Declarative models for the NSE Screener database.

All models derive from a shared :data:`Base` so that
``Base.metadata.create_all()`` can bootstrap every table in one call.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Column,
    Date,
    DateTime,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase


# ---------------------------------------------------------------------------
# Declarative base (SQLAlchemy 2.x style)
# ---------------------------------------------------------------------------
class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""

    pass


# ---------------------------------------------------------------------------
# Daily price model (recreated task D4: raw-first, series, market_cap, isin)
# ---------------------------------------------------------------------------
class DailyPrice(Base):
    """OHLCV record for a single stock on a single trading day.

    The composite unique constraint ``(symbol, date)`` guarantees strict
    deduplication at the database level — duplicate inserts are safely
    handled via ``INSERT … ON DUPLICATE KEY UPDATE`` in the ETL loader.
    """

    __tablename__ = "daily_prices"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)
    series = Column(String(4), nullable=False, default="EQ", server_default=text("'EQ'"))
    isin = Column(String(12), nullable=True)
    open = Column(Numeric(10, 2), nullable=True)
    high = Column(Numeric(10, 2), nullable=True)
    low = Column(Numeric(10, 2), nullable=True)
    close = Column(Numeric(10, 2), nullable=True)
    prev_close = Column(Numeric(10, 2), nullable=True)
    volume = Column(BigInteger, nullable=True)
    market_cap = Column(Numeric(20, 2), nullable=True)

    # Corporate-action adjusted price and volume columns
    adj_open = Column(Numeric(14, 4), nullable=True)
    adj_high = Column(Numeric(14, 4), nullable=True)
    adj_low = Column(Numeric(14, 4), nullable=True)
    adj_close = Column(Numeric(14, 4), nullable=True)
    adj_volume = Column(BigInteger, nullable=True)

    __table_args__ = (UniqueConstraint("symbol", "date", name="uq_daily_prices_symbol_date"),)

    def __repr__(self) -> str:
        return (
            f"<DailyPrice(symbol={self.symbol!r}, date={self.date}, "
            f"series={self.series!r}, close={self.close})>"
        )


# ---------------------------------------------------------------------------
# Securities master model (task D4)
# ---------------------------------------------------------------------------
class Security(Base):
    """Master registry of listed securities from official NSE reports."""

    __tablename__ = "securities"

    symbol = Column(String(20), primary_key=True)
    isin = Column(String(12), nullable=True)
    name = Column(String(200), nullable=True)
    series = Column(String(4), nullable=True)
    listing_date = Column(Date, nullable=True)
    first_seen = Column(Date, nullable=True)
    last_seen = Column(Date, nullable=True)
    status = Column(
        String(10),
        nullable=False,
        default="active",
        server_default=text("'active'"),
    )

    def __repr__(self) -> str:
        return (
            f"<Security(symbol={self.symbol!r}, isin={self.isin!r}, "
            f"series={self.series!r}, status={self.status!r})>"
        )


# ---------------------------------------------------------------------------
# Corporate-action adjustment factors
# ---------------------------------------------------------------------------
class AdjFactor(Base):
    """Detected split, bonus, and corporate-action adjustment factors."""

    __tablename__ = "adj_factors"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)
    factor = Column(Numeric(18, 8), nullable=False)
    ratio_kind = Column(String(20), nullable=False)
    detected_at = Column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    __table_args__ = (UniqueConstraint("symbol", "date", name="uq_adj_factors_symbol_date"),)

    def __repr__(self) -> str:
        return (
            f"<AdjFactor(symbol={self.symbol!r}, date={self.date}, "
            f"factor={self.factor}, kind={self.ratio_kind!r})>"
        )


# ---------------------------------------------------------------------------
# Benchmark index prices
# ---------------------------------------------------------------------------
class BenchmarkPrice(Base):
    """Daily benchmark closing price for market indices."""

    __tablename__ = "benchmark_prices"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    index_code = Column(String(40), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)
    close = Column(Numeric(14, 4), nullable=False)
    source = Column(String(20), nullable=False)

    __table_args__ = (UniqueConstraint("index_code", "date", name="uq_benchmark_code_date"),)

    def __repr__(self) -> str:
        return (
            f"<BenchmarkPrice(index={self.index_code!r}, date={self.date}, "
            f"close={self.close}, source={self.source!r})>"
        )


# ---------------------------------------------------------------------------
# Official NSE corporate actions and symbol changes (task CA1)
# ---------------------------------------------------------------------------
class CorporateAction(Base):
    """One action from NSE's corporate-actions report, one row per action part.

    A subject such as "Bonus 1:1/Face Value Split ..." holds two actions, so it becomes
    two rows (``part_no`` 0 and 1). ``price_factor`` is the number to multiply prices
    BEFORE the ex-date by; it is only set when the ratio is exact (``parse_status`` =
    'exact'). Rows marked 'needs_review' are never applied automatically.
    """

    __tablename__ = "corporate_actions"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    symbol = Column(String(50), nullable=False, index=True)
    ex_date = Column(Date, nullable=False, index=True)
    series = Column(String(10), nullable=False)
    isin = Column(String(20), nullable=True)
    company = Column(String(255), nullable=True)
    face_value = Column(Numeric(14, 4), nullable=True)
    record_date = Column(Date, nullable=True)
    action_type = Column(String(30), nullable=False, index=True)
    part_no = Column(Integer, nullable=False, default=0, server_default=text("0"))
    subject = Column(String(500), nullable=False)
    # md5 of the normalised subject: a short, stable stand-in for the long text in the key.
    subject_hash = Column(String(32), nullable=False)
    old_face_value = Column(Numeric(14, 4), nullable=True)
    new_face_value = Column(Numeric(14, 4), nullable=True)
    bonus_new_shares = Column(Numeric(14, 4), nullable=True)
    bonus_held_shares = Column(Numeric(14, 4), nullable=True)
    rights_new = Column(Numeric(14, 4), nullable=True)
    rights_held = Column(Numeric(14, 4), nullable=True)
    price_factor = Column(Numeric(18, 8), nullable=True)
    parse_status = Column(String(20), nullable=False)
    fetched_at = Column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        UniqueConstraint(
            "symbol",
            "ex_date",
            "series",
            "subject_hash",
            "part_no",
            name="uq_corporate_actions_key",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<CorporateAction(symbol={self.symbol!r}, ex_date={self.ex_date}, "
            f"type={self.action_type!r}, factor={self.price_factor})>"
        )


class SymbolChange(Base):
    """Official NSE symbol change (``symbolchange.csv``), e.g. ICICIPRULI -> ICICILIFE.

    This is evidence only. Raw prices are never relabelled: a renamed stock's history is
    joined at read time by ``src.ingestion.renames.prices_for``.
    """

    __tablename__ = "symbol_changes"

    old_symbol = Column(String(50), primary_key=True)
    new_symbol = Column(String(50), primary_key=True)
    effective_date = Column(Date, primary_key=True)
    company = Column(String(255), nullable=True)

    def __repr__(self) -> str:
        return (
            f"<SymbolChange(old={self.old_symbol!r}, new={self.new_symbol!r}, "
            f"date={self.effective_date})>"
        )
