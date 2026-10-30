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
