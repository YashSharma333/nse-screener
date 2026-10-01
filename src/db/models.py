from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import Column, BigInteger, String, Date, Numeric, UniqueConstraint

class Base(DeclarativeBase):
    pass

class DailyPrice(Base):
    """OHLCV record for a single stock on a single trading day."""
    __tablename__ = "daily_prices"

    #Primary Key
    id = Column(BigInteger, autoincrement=True, primary_key=True)

    # Indexed columns for fast querying
    date = Column(Date, index=True, nullable=False)
    symbol = Column(String(50), index=True, nullable=False)

    # Exact decimal precision (12 digits total, 2 decimal places)
    open = Column(Numeric(12, 2), nullable=False)
    high = Column(Numeric(12, 2), nullable=False)
    low = Column(Numeric(12, 2), nullable=False)
    close = Column(Numeric(12, 2), nullable=False)

    # Volume stored as BigInteger to handle high-liquidity index constituents
    volume = Column(BigInteger, nullable=False)
    
    __table_args__ = (
        UniqueConstraint('date', 'symbol', name='uq_symbol_date'),
    )
    pass

    def __repr__(self):
        return f"<DailyPrice(symbol='{self.symbol}', date='{self.date}', close={self.close})>"

class TechnicalIndicator(Base):
    """Pre-computed technical indicators per stock per day."""
    __tablename__ = "technical_indicators"

    # Primary key
    id = Column(BigInteger, autoincrement=True, primary_key=True)

    # Indexed columns for fast querying
    date = Column(Date, index=True, nullable=False)
    symbol = Column(String(50), index=True, nullable=False)
    
    # Moving Averages
    sma_20 = Column(Numeric(12, 2), nullable=False)
    sma_50 = Column(Numeric(12, 2), nullable=False)
    sma_200 = Column(Numeric(12, 2), nullable=False)

    # Exponential Moving Averages
    ema_10 = Column(Numeric(12, 2), nullable=False)
    ema_20 = Column(Numeric(12, 2), nullable=False)
    ema_50 = Column(Numeric(12, 2), nullable=False)

    # Momentum, Volume, and Volatility
    rsi_14 = Column(Numeric(12, 2), nullable=True)
    vwap = Column(Numeric(12, 2), nullable=True)
    bb_upper = Column(Numeric(12, 2), nullable=True)
    bb_lower = Column(Numeric(12, 2), nullable=True)
    rs_line = Column(Numeric(12, 2), nullable=True)
    hmm = Column(Numeric(12, 2), nullable=True) # Hillega Millega(rsi modified)

    # Prevent duplicate indicator records
    __table_args__ = (
        UniqueConstraint('date', 'symbol', name='uq_tech_symbol_date')
    )
    pass

    def __repr__(self):
        return f"<TechnicalIndicator(symbol='{self.symbol}', date='{self.date}', rsi={self.rsi_14})>"