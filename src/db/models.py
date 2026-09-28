from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import Column, BigInteger, String, Date, Numeric, UniqueConstraint

class Base(DeclarativeBase):
    pass

class DailyPrice(Base):
    """OHLCV record for a single stock on a single trading day."""
    __tablename__ = "daily_prices"
    
    # TODO: Define the columns for the daily price model.
    # HINT: You'll need id, symbol, date, open, high, low, close, volume.
    # HINT: Use Column(String(20)) for symbol, Column(Numeric(10, 2)) for prices.
    
    # TODO: Add a unique constraint to prevent duplicate entries for the same symbol on the same date.
    # HINT: Use __table_args__ = (UniqueConstraint(...),)
    pass

class TechnicalIndicator(Base):
    """Pre-computed technical indicators per stock per day."""
    __tablename__ = "technical_indicators"
    
    # TODO: Define the base columns (id, symbol, date).
    # TODO: Add columns for technical indicators like sma_50, sma_200, rsi, etc.
    pass