"""
Feature engine — kontrak dict fitur yang dipakai semua strategi.
"compute once, reuse everywhere": jangan hitung indikator mentah lagi di
strategy_*.py, baca dari sini.
"""
import pandas_ta as ta


def compute_features(ticker, df, spy_df):
    if len(df) < 200:
        return None

    close = df["Close"]
    df["MA20"] = close.rolling(20).mean()
    df["MA50"] = close.rolling(50).mean()
    df["MA200"] = close.rolling(200).mean()
    df["RSI14"] = ta.rsi(close, length=14)
    df["ATR14"] = ta.atr(df["High"], df["Low"], close, length=14)
    df["VOL_AVG20"] = df["Volume"].rolling(20).mean()

    last = df.iloc[-1]
    if last[["MA20", "MA50", "MA200", "RSI14", "ATR14"]].isna().any():
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
