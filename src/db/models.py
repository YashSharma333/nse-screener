from sqlalchemy import Column, Integer, String, Float, Date, Boolean
from sqlalchemy.orm import declarative_base

Base = declarative_base()

class DailyPriceRecord(Base):
    """ORM Model for daily stock OHLCV data."""
    __tablename__ = 'daily_prices'

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(50), index=True, nullable=False)
    date = Column(Date, index=True, nullable=False)
    
    # TODO: Define remaining columns for Open, High, Low, Close, Volume
    
class TechnicalIndicator(Base):
    """ORM Model for pre-computed technical indicators."""
    __tablename__ = 'technical_indicators'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(50), index=True, nullable=False)
    date = Column(Date, index=True, nullable=False)
    
    # TODO: Define columns for indicators (e.g., sma_50, sma_200, rsi, is_52w_high)