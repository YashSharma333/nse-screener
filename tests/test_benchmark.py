"""Unit tests for benchmark index ingestion layer (src/ingestion/benchmark.py)."""

from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

from src.ingestion.benchmark import NSEIndexSource

FIXTURE_PATH = Path("tests/fixtures/ind_close_all_sample.csv")


def test_nse_index_source_parses_sample_fixture():
    """NSEIndexSource must parse all 5 required indices from sample CSV."""
    with FIXTURE_PATH.open() as f:
        csv_text = f.read()

    mock_session = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = csv_text
    mock_session.get.return_value = mock_resp

    source = NSEIndexSource(session=mock_session)
    d = date(2024, 10, 25)
    prices = source.fetch_prices(d)

    assert prices is not None
    assert "NIFTY 50" in prices
    assert prices["NIFTY 50"] == 24180.8
    assert "NIFTY 100" in prices
    assert prices["NIFTY 100"] == 25022.2
    assert "NIFTY MIDCAP 150" in prices
    assert prices["NIFTY MIDCAP 150"] == 20465.5
    assert "NIFTY SMALLCAP 250" in prices
    assert prices["NIFTY SMALLCAP 250"] == 17250.0
    assert "NIFTY 500" in prices
    assert prices["NIFTY 500"] == 22499.05


def test_nse_index_source_handles_404_gracefully():
    """When NSE archive returns 404 (e.g. holiday), source returns None."""
    mock_session = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_session.get.return_value = mock_resp

    source = NSEIndexSource(session=mock_session)
    d = date(2024, 10, 26)
    prices = source.fetch_prices(d)
    assert prices is None
