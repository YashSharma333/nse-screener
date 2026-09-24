import os
import logging
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.db.models import Base

logger = logging.getLogger(__name__)

# Use SQLite for local development; swap to cloud DB URL in production via .env
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/processed/screener.db")

try:
    engine = create_engine(DATABASE_URL, echo=False)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
except Exception as e:
    logger.error(f"Database connection failed: {e}")
    raise

def init_db() -> None:
    """Creates all tables defined in models.py."""
    try:
        # TODO: Insert logic here to create all metadata tables
        pass
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        raise

def get_db_session():
    """Dependency to yield database session and ensure closure."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()