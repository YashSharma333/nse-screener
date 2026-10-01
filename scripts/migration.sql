-- ==============================================================================
-- Migration: Add index_name column to daily_prices table
-- ==============================================================================
-- For MySQL 8.0+:
-- ALTER TABLE daily_prices 
-- ADD COLUMN IF NOT EXISTS index_name VARCHAR(50) NULL,
-- ADD INDEX IF NOT EXISTS ix_daily_prices_index_name (index_name);

-- Standard SQL syntax (compatible across all MySQL versions):
ALTER TABLE daily_prices ADD COLUMN index_name VARCHAR(50) NULL;
CREATE INDEX ix_daily_prices_index_name ON daily_prices (index_name);
