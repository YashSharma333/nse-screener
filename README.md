# NSE Stock Screener & Quantitative Analytics Engine

A modular, high-performance data ingestion and technical analysis pipeline for Indian equities (NSE). The engine automatically downloads, caches, and transforms daily NSE Bhavcopy records, filters them across SEBI market-cap tiers (Large, Mid, and Small Cap), and stores clean OHLCV data in a relational MySQL database.

## Architecture

- **`src/ingestion/`**: Fetches official NSE index constituent lists (`NSEIndexConstituents`) and downloads/parses daily Bhavcopy files (`NSEBhavcopyDownloader`) with retry logic and session warm-up.
- **`src/etl/`**: Orchestrates incremental updates, deduplication, and database inserts (`BhavcopyETL`).
- **`src/db/`**: SQLAlchemy models and database connection sessions.
- **`config/`**: Centralized rotating log configuration and environment settings.
- **`data/raw/`**: Local caching layer using Parquet for instant re-runs and minimal network overhead.

## Setup & Installation

1. **Clone the repository:**
   ```bash
   git clone [https://github.com/](https://github.com/)<your-username>/nse-screener.git
   cd nse-screener