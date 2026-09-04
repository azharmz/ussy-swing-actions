"""
Fetch layer — ambil harga dari yfinance secara batch.
Tidak pakai file cache (beda dari versi Colab) karena GitHub Actions runner
selalu fresh setiap run, jadi cache lokal tidak ada gunanya di sini.
"""
import time
import pandas as pd


REQUIRED_PRICE_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]


def _clean_price_frame(df):
    """Keep only complete, numeric and chronologically unique OHLCV bars.

    yfinance can temporarily include a partially populated latest row. Keeping
    that row makes every trailing indicator NaN even though the older history
    is healthy, so validation must be per required column rather than how='all'.
    """
    if df is None or df.empty or not set(REQUIRED_PRICE_COLUMNS).issubset(df.columns):
        return None
    result = df.copy()
    for column in REQUIRED_PRICE_COLUMNS:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    result = result.replace([float("inf"), float("-inf")], pd.NA)
    result = result.dropna(subset=REQUIRED_PRICE_COLUMNS)
    result = result[~result.index.duplicated(keep="last")].sort_index()
    return result if len(result) >= 200 else None


def fetch_prices(tickers, period="1y", batch_size=50, pause_sec=2):
    import yfinance as yf

    data = {}
    rejected = []
    for i in range(0, len(tickers), batch_size):
        batch = tickers[i:i + batch_size]
        print(f"Fetch batch {i // batch_size + 1} ({len(batch)} ticker)...")
        raw = yf.download(
            batch,
            period=period,
            interval="1d",
            group_by="ticker",
            threads=True,
            progress=False,
            auto_adjust=True,
        )
        for t in batch:
            try:
                source = raw[t] if len(batch) > 1 else raw
                df = _clean_price_frame(source)
                if df is not None:
                    data[t] = df
                else:
                    rejected.append(t)
            except (KeyError, Exception) as e:
                print(f"Skip {t}: {e}")
                rejected.append(t)
        if i + batch_size < len(tickers):
            time.sleep(pause_sec)
    if "SPY" in data:
        spy = data["SPY"]
        print(f"SPY OHLCV valid: {len(spy)} bar, terakhir {spy.index[-1].date()}")
    if rejected:
        print(f"Fetch validation: {len(rejected)} ticker ditolak karena OHLCV lengkap <200 bar "
              f"atau kolom tidak valid. Contoh: {rejected[:10]}")
    return data
