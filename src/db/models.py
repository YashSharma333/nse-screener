from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import Column, BigInteger, String, Date, Numeric, UniqueConstraint

class Base(DeclarativeBase):
    pass

class DailyPrice(Base):
    """OHLCV record for a single stock on a single trading day."""
    __tablename__ = "daily_prices"
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    symbol = Column(String(50), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)
    open = Column(Numeric(12, 2), nullable=False)
    high = Column(Numeric(12, 2), nullable=False)
    low = Column(Numeric(12, 2), nullable=False)
    close = Column(Numeric(12, 2), nullable=False)
    volume = Column(BigInteger, nullable=False)
    
    __table_args__ = (
        UniqueConstraint('symbol', 'date', name='uq_daily_price_symbol_date'),
    )

class TechnicalIndicator(Base):
    """Pre-computed technical indicators per stock per day."""
    __tablename__ = "technical_indicators"
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    symbol = Column(String(50), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)
    
    sma_50 = Column(Numeric(12, 2), nullable=True)
    sma_200 = Column(Numeric(12, 2), nullable=True)
    high_52w = Column(Numeric(12, 2), nullable=True)
    rsi_14 = Column(Numeric(12, 2), nullable=True)

    __table_args__ = (
        UniqueConstraint('symbol', 'date', name='uq_technical_ind_symbol_date'),
    )