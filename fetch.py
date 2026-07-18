"""
Fetch layer — ambil harga dari yfinance secara batch.
Tidak pakai file cache (beda dari versi Colab) karena GitHub Actions runner
selalu fresh setiap run, jadi cache lokal tidak ada gunanya di sini.
"""
import time
import yfinance as yf


def fetch_prices(tickers, period="1y", batch_size=50, pause_sec=2):
    data = {}
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
                df = raw[t].dropna(how="all") if len(batch) > 1 else raw.dropna(how="all")
                if not df.empty and len(df) > 200:
                    data[t] = df
            except (KeyError, Exception) as e:
                print(f"Skip {t}: {e}")
        if i + batch_size < len(tickers):
            time.sleep(pause_sec)
    return data
