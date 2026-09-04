import unittest

import pandas as pd

from feature_engine import _atr, _rsi, compute_features
from fetch import _clean_price_frame


def _prices(rows=220):
    index = pd.bdate_range("2025-01-01", periods=rows)
    close = pd.Series([100 + i * 0.2 for i in range(rows)], index=index)
    return pd.DataFrame({
        "Open": close - 0.1,
        "High": close + 1.0,
        "Low": close - 1.0,
        "Close": close,
        "Volume": 1_000_000,
    }, index=index)


class PriceCleaningTests(unittest.TestCase):
    def test_partial_latest_row_is_removed(self):
        frame = _prices()
        partial_date = frame.index[-1] + pd.offsets.BDay(1)
        frame.loc[partial_date] = [None, None, None, None, 0]
        clean = _clean_price_frame(frame)
        self.assertEqual(len(clean), 220)
        self.assertEqual(clean.index[-1], frame.index[-2])

    def test_too_little_complete_history_is_rejected(self):
        self.assertIsNone(_clean_price_frame(_prices(199)))


class FeatureTests(unittest.TestCase):
    def test_native_indicators_produce_valid_latest_values(self):
        frame = _prices()
        self.assertFalse(pd.isna(_rsi(frame["Close"]).iloc[-1]))
        self.assertFalse(pd.isna(_atr(frame["High"], frame["Low"], frame["Close"]).iloc[-1]))

    def test_compute_features_with_complete_history(self):
        frame = _prices()
        features = compute_features("TEST", frame, frame.copy())
        self.assertIsNotNone(features)
        self.assertEqual(features["ticker"], "TEST")
        self.assertFalse(pd.isna(features["rsi14"]))
        self.assertFalse(pd.isna(features["atr14_pct"]))


if __name__ == "__main__":
    unittest.main()
