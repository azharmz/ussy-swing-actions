"""
Feature engine — kontrak dict fitur yang dipakai semua strategi.
"compute once, reuse everywhere": jangan hitung indikator mentah lagi di
strategy_*.py, baca dari sini.
"""
import pandas as pd


def _rma(series, length):
    """Wilder/RMA smoothing used by the previous pandas-ta implementation."""
    return series.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()


def _rsi(close, length=14):
    delta = close.diff()
    positive = delta.clip(lower=0)
    negative = -delta.clip(upper=0)
    positive_avg = _rma(positive, length)
    negative_avg = _rma(negative, length)
    denominator = positive_avg + negative_avg
    return 100 * positive_avg / denominator


def _atr(high, low, close, length=14):
    previous_close = close.shift(1)
    true_range = pd.concat([
        high - low,
        (high - previous_close).abs(),
        (low - previous_close).abs(),
    ], axis=1).max(axis=1)
    # Tidak ada previous close untuk bar pertama; samakan konvensi pandas-ta
    # lama agar hasil production/backtest tidak bergeser satu observasi.
    true_range.iloc[0] = pd.NA
    return _rma(true_range, length)


def compute_features(ticker, df, spy_df):
    if len(df) < 200:
        return None

    close = df["Close"]
    df["MA20"] = close.rolling(20).mean()
    df["MA50"] = close.rolling(50).mean()
    df["MA200"] = close.rolling(200).mean()
    df["RSI14"] = _rsi(close, length=14)
    df["ATR14"] = _atr(df["High"], df["Low"], close, length=14)
    df["VOL_AVG20"] = df["Volume"].rolling(20).mean()

    last = df.iloc[-1]
    required = ["Close", "Volume", "MA20", "MA50", "MA200", "RSI14", "ATR14", "VOL_AVG20"]
    if last[required].isna().any():
        missing = [name for name in required if pd.isna(last[name])]
        print(f"Skip fitur {ticker}: indikator/bar terakhir tidak valid {missing} "
              f"(rows={len(df)}, last={df.index[-1].date()})")
        return None

    stock_ret_20d = (close.iloc[-1] / close.iloc[-21] - 1) * 100
    spy_ret_20d = (spy_df["Close"].iloc[-1] / spy_df["Close"].iloc[-21] - 1) * 100
    relative_strength = round(stock_ret_20d - spy_ret_20d, 2)

    relative_volume = round(last["Volume"] / last["VOL_AVG20"], 2) if last["VOL_AVG20"] > 0 else None
    atr_pct = round((last["ATR14"] / last["Close"]) * 100, 2)
    dist_ma20_pct = round(((last["Close"] - last["MA20"]) / last["MA20"]) * 100, 2)

    return {
        "ticker": ticker,
        "date": str(df.index[-1].date()),
        "close": round(float(last["Close"]), 2),
        "ma20": round(float(last["MA20"]), 2),
        "ma50": round(float(last["MA50"]), 2),
        "ma200": round(float(last["MA200"]), 2),
        "rsi14": round(float(last["RSI14"]), 2),
        "atr14_pct": atr_pct,
        "relative_volume": relative_volume,
        "relative_strength_spy": relative_strength,
        "dist_ma20_pct": dist_ma20_pct,
    }
