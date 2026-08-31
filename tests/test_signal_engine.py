import unittest

import pandas as pd

from signal_engine import _bars_after, _gap_aware_stop_price, _process_signal_bar


class _Result:
    data = []


class _Table:
    def __init__(self):
        self.payloads = []

    def update(self, payload):
        self.payloads.append(payload)
        return self

    def eq(self, *_args):
        return self

    def execute(self):
        return _Result()


class _Supabase:
    def __init__(self):
        self.target = _Table()

    def table(self, _name):
        return self.target


def _bar(open_, high, low, close):
    return pd.Series({"Open": open_, "High": high, "Low": low, "Close": close})


def _pending(strategy="pullback"):
    return {
        "id": 1,
        "ticker": "TEST",
        "strategy": strategy,
        "status": "pending",
        "signal_date": "2026-08-20",
        "last_processed_date": "2026-08-20",
        "days_in_status": 0,
        "entry_price": 100.0,
        "stop_loss": 90.0,
        "take_profit": 120.0,
        "lowest_price": 95.0,
        "highest_price": 105.0,
    }


class SignalLifecycleTests(unittest.TestCase):
    def test_bars_after_is_idempotent_and_replays_all_missing_dates(self):
        df = pd.DataFrame(
            {"Close": [1, 2, 3]},
            index=pd.to_datetime(["2026-08-20", "2026-08-21", "2026-08-24"]),
        )
        unseen = _bars_after(df, "2026-08-20")
        self.assertEqual(list(unseen.index.date), [
            pd.Timestamp("2026-08-21").date(),
            pd.Timestamp("2026-08-24").date(),
        ])
        self.assertTrue(_bars_after(df, "2026-08-24").empty)

    def test_same_bar_entry_and_stop_is_recorded_as_loss(self):
        db = _Supabase()
        sig = _pending("pullback")
        ok = _process_signal_bar(
            db, sig, _bar(100, 103, 89, 95), "2026-08-21", "below", "TEST/pullback"
        )
        self.assertTrue(ok)
        self.assertEqual(sig["status"], "sl_hit")
        self.assertEqual(sig["entry_date"], "2026-08-21")
        self.assertEqual(sig["exit_date"], "2026-08-21")
        self.assertLess(sig["pnl_pct"], 0)

    def test_breakout_stop_without_entry_remains_missed(self):
        db = _Supabase()
        sig = _pending("breakout")
        _process_signal_bar(
            db, sig, _bar(95, 99, 89, 92), "2026-08-21", "above", "TEST/breakout"
        )
        self.assertEqual(sig["status"], "missed")
        self.assertEqual(sig["missed_reason"], "sl_hit_before_entry")

    def test_gap_through_stop_fills_at_open(self):
        self.assertEqual(_gap_aware_stop_price(_bar(85, 88, 80, 82), 90), 85.0)
        self.assertEqual(_gap_aware_stop_price(_bar(95, 98, 89, 92), 90), 90.0)


if __name__ == "__main__":
    unittest.main()

