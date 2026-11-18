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
    Boolean,
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
# Daily price model
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
    open = Column(Numeric(10, 2), nullable=True)
    high = Column(Numeric(10, 2), nullable=True)
    low = Column(Numeric(10, 2), nullable=True)
    close = Column(Numeric(10, 2), nullable=True)
    volume = Column(BigInteger, nullable=True)
    prev_close = Column(Numeric(10, 2), nullable=True)
    index_name = Column(String(50), nullable=True, index=True)

    # Corporate-action adjusted price and volume columns
    adj_open = Column(Numeric(14, 4), nullable=True)
    adj_high = Column(Numeric(14, 4), nullable=True)
    adj_low = Column(Numeric(14, 4), nullable=True)
    adj_close = Column(Numeric(14, 4), nullable=True)
    adj_volume = Column(BigInteger, nullable=True)

    __table_args__ = (UniqueConstraint("symbol", "date", name="uq_symbol_date"),)

    def __repr__(self) -> str:
        return (
            f"<DailyPrice(symbol={self.symbol!r}, date={self.date}, "
            f"close={self.close}, index={self.index_name!r})>"
        )


# ---------------------------------------------------------------------------
# Technical indicator model (UNUSED: legacy table preserved for schema compatibility)
# ---------------------------------------------------------------------------
class TechnicalIndicator(Base):
    """Pre-computed technical indicators per stock per day.

    Columns will be added as the computation layer is built out
    (e.g. ``sma_50``, ``sma_200``, ``rsi``, ``is_52w_high``).
    """

    __tablename__ = "technical_indicators"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)

    # Moving averages
    sma_50 = Column(Numeric(10, 2), nullable=True)
    sma_200 = Column(Numeric(10, 2), nullable=True)
    ema_12 = Column(Numeric(10, 2), nullable=True)
    ema_26 = Column(Numeric(10, 2), nullable=True)

    # Momentum
    rsi_14 = Column(Numeric(10, 4), nullable=True)

    # Range
    high_52w = Column(Numeric(10, 2), nullable=True)
    low_52w = Column(Numeric(10, 2), nullable=True)

    # Bollinger Bands
    bb_upper = Column(Numeric(10, 2), nullable=True)
    bb_middle = Column(Numeric(10, 2), nullable=True)
    bb_lower = Column(Numeric(10, 2), nullable=True)

    # MACD
    macd_line = Column(Numeric(10, 4), nullable=True)
    macd_signal = Column(Numeric(10, 4), nullable=True)
    macd_histogram = Column(Numeric(10, 4), nullable=True)

    # Volatility
    daily_returns = Column(Numeric(10, 6), nullable=True)
    volatility_20 = Column(Numeric(10, 6), nullable=True)

    __table_args__ = (UniqueConstraint("symbol", "date", name="uq_indicator_symbol_date"),)

    def __repr__(self) -> str:
        return f"<TechnicalIndicator(symbol={self.symbol!r}, date={self.date})>"


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
# Historical index membership
# ---------------------------------------------------------------------------
class IndexMembership(Base):
    """Historical point-in-time snapshot and validity range of index constituents."""

    __tablename__ = "index_membership"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False, index=True)
    index_code = Column(String(50), nullable=False, index=True)
    valid_from = Column(Date, nullable=False, index=True)
    valid_to = Column(Date, nullable=True)
    created_at = Column(DateTime, server_default=text("CURRENT_TIMESTAMP"), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "symbol",
            "index_code",
            "valid_from",
            name="uq_membership_symbol_code_valid_from",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<IndexMembership(symbol={self.symbol!r}, index={self.index_code!r}, "
            f"valid_from={self.valid_from}, valid_to={self.valid_to})>"
        )


# ---------------------------------------------------------------------------
# Stock master and universe model
# ---------------------------------------------------------------------------
class StockMaster(Base):
    """Master registry of stocks, classifications, and universe inclusion status.

    Tracks market capitalization, sector, industry, cap bucket, and universe
    membership with hysteresis thresholds.
    """

    __tablename__ = "stock_master"

    symbol = Column(String(50), primary_key=True)
    company_name = Column(String(255), nullable=False)
    sector = Column(String(100), nullable=True)
    industry = Column(String(100), nullable=True)
    cap_bucket = Column(String(50), nullable=True)
    market_cap_cr = Column(Numeric(14, 2), nullable=True)
    market_cap_asof = Column(Date, nullable=True)
    in_universe = Column(
        Boolean, nullable=False, default=False, server_default=text("0"), index=True
    )
    status = Column(String(30), nullable=False, index=True)
    added_on = Column(Date, nullable=False)
    flagged_on = Column(Date, nullable=True)
    benchmark_index = Column(String(50), nullable=True)
    source = Column(String(100), nullable=True)
    renamed_to = Column(String(50), nullable=True)

    def __repr__(self) -> str:
        return (
            f"<StockMaster(symbol={self.symbol!r}, company={self.company_name!r}, "
            f"in_universe={self.in_universe}, status={self.status!r}, "
            f"market_cap_cr={self.market_cap_cr})>"
        )


# ---------------------------------------------------------------------------
# Symbol alias and rename mapping model
# ---------------------------------------------------------------------------
class SymbolAlias(Base):
    """Symbol rename and alias mapping.

    Tracks corporate symbol changes (e.g. ICICIPRULI -> ICICILIFE) with continuity
    verification status and implied price ratio.
    """

    __tablename__ = "symbol_alias"

    old_symbol = Column(String(50), primary_key=True)
    new_symbol = Column(String(50), primary_key=True)
    effective_date = Column(Date, nullable=True)
    status = Column(
        String(30), nullable=False, default="candidate", server_default=text("'candidate'")
    )
    implied_ratio = Column(Numeric(14, 4), nullable=True)
    note = Column(String(255), nullable=True)

    def __repr__(self) -> str:
        return (
            f"<SymbolAlias(old={self.old_symbol!r}, new={self.new_symbol!r}, "
            f"status={self.status!r}, ratio={self.implied_ratio})>"
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

    This is evidence only. It does not merge any price history: renames are applied
    through ``symbol_alias`` after the price-continuity check.
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
