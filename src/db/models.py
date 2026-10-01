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
    Index,
    Numeric,
    String,
    UniqueConstraint,
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

    id         = Column(BigInteger, primary_key=True, autoincrement=True)
    symbol     = Column(String(20), nullable=False, index=True)
    date       = Column(Date,       nullable=False, index=True)
    open       = Column(Numeric(10, 2), nullable=True)
    high       = Column(Numeric(10, 2), nullable=True)
    low        = Column(Numeric(10, 2), nullable=True)
    close      = Column(Numeric(10, 2), nullable=True)
    volume     = Column(BigInteger,     nullable=True)
    index_name = Column(String(50),     nullable=True, index=True)

    __table_args__ = (
        UniqueConstraint("symbol", "date", name="uq_symbol_date"),
    )

    def __repr__(self) -> str:
        return (
            f"<DailyPrice(symbol={self.symbol!r}, date={self.date}, "
            f"close={self.close}, index={self.index_name!r})>"
        )


# ---------------------------------------------------------------------------
# Technical indicator model (placeholder for future use)
# ---------------------------------------------------------------------------
class TechnicalIndicator(Base):
    """Pre-computed technical indicators per stock per day.

    Columns will be added as the computation layer is built out
    (e.g. ``sma_50``, ``sma_200``, ``rsi``, ``is_52w_high``).
    """

    __tablename__ = "technical_indicators"

    id     = Column(BigInteger, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False, index=True)
    date   = Column(Date,       nullable=False, index=True)

    # Moving averages
    sma_50  = Column(Numeric(10, 2), nullable=True)
    sma_200 = Column(Numeric(10, 2), nullable=True)
    ema_12  = Column(Numeric(10, 2), nullable=True)
    ema_26  = Column(Numeric(10, 2), nullable=True)

    # Momentum
    rsi_14 = Column(Numeric(10, 4), nullable=True)

    # Range
    high_52w = Column(Numeric(10, 2), nullable=True)
    low_52w  = Column(Numeric(10, 2), nullable=True)

    # Bollinger Bands
    bb_upper  = Column(Numeric(10, 2), nullable=True)
    bb_middle = Column(Numeric(10, 2), nullable=True)
    bb_lower  = Column(Numeric(10, 2), nullable=True)

    # MACD
    macd_line      = Column(Numeric(10, 4), nullable=True)
    macd_signal    = Column(Numeric(10, 4), nullable=True)
    macd_histogram = Column(Numeric(10, 4), nullable=True)

    # Volatility
    daily_returns = Column(Numeric(10, 6), nullable=True)
    volatility_20 = Column(Numeric(10, 6), nullable=True)

    __table_args__ = (
        UniqueConstraint("symbol", "date", name="uq_indicator_symbol_date"),
    )

    def __repr__(self) -> str:
        return (
            f"<TechnicalIndicator(symbol={self.symbol!r}, date={self.date})>"
        )