"""Unit tests for xtdata loader (all mocked, no real xtquant)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from backtest.loaders.base import NoAvailableSourceError


class TestXtDataLoader:
    """Loader registration and behaviour."""

    def test_loader_registered(self):
        from backtest.loaders.registry import LOADER_REGISTRY, _ensure_registered
        _ensure_registered()
        assert "xtdata" in LOADER_REGISTRY

    def test_loader_name(self):
        from backtest.loaders.xtdata_loader import XtDataLoader
        assert XtDataLoader.name == "xtdata"

    def test_period_map(self):
        from backtest.loaders.xtdata_loader import XtDataLoader
        loader = XtDataLoader()
        assert loader.PERIOD_MAP["1d"] == "1d"
        assert loader.PERIOD_MAP["1h"] == "60m"
        assert loader.PERIOD_MAP["1M"] == "1mon"

    @patch("backtest.loaders.xtdata_loader.validate_ohlc")
    def test_load_returns_dataframe(self, mock_validate):
        """Simulate a successful load with mocked xtdata."""
        import numpy as np

        mock_validate.side_effect = lambda df: df

        mock_df = pd.DataFrame(
            {
                "open": [10.0, 10.5],
                "high": [11.0, 11.5],
                "low": [9.5, 10.0],
                "close": [10.5, 11.0],
                "volume": [1000000, 1200000],
                "amount": [10500000.0, 12600000.0],
            },
            index=pd.date_range("2024-01-01", periods=2, freq="D"),
        )

        mock_data = {"000001.SZ": mock_df}

        with patch("backtest.loaders.xtdata_loader._xtdata_lock"):
            with patch("xtquant.xtdata.download_history_data"):
                with patch("xtquant.xtdata.get_local_data", return_value=mock_data):
                    from backtest.loaders.xtdata_loader import XtDataLoader
                    loader = XtDataLoader()
                    df = loader.load("000001.SZ", "20240101", "20240105")
                    assert isinstance(df, pd.DataFrame)
                    # loader keeps "amount" column when present
                    assert list(df.columns) == ["open", "high", "low", "close", "volume", "amount"]

    def test_load_no_data_raises(self):
        """No data returned → NoAvailableSourceError."""
        with patch("xtquant.xtdata.download_history_data"):
            with patch("xtquant.xtdata.get_local_data", return_value=None):
                from backtest.loaders.xtdata_loader import XtDataLoader
                loader = XtDataLoader()
                with pytest.raises(NoAvailableSourceError):
                    loader.load("000001.SZ", "20240101", "20240105")

    def test_load_import_error_raises(self):
        """xtquant not installed → NoAvailableSourceError."""
        # Patch after the loader class is already imported, only block
        # xtquant.xtdata from being importable inside load().
        from backtest.loaders.xtdata_loader import XtDataLoader
        loader = XtDataLoader()

        def _fake_import(name, *args, **kwargs):
            if name == "xtquant.xtdata":
                raise ImportError("no xtquant")
            return __import__(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_fake_import):
            with pytest.raises(NoAvailableSourceError, match="not installed"):
                loader.load("000001.SZ", "20240101", "20240105")
