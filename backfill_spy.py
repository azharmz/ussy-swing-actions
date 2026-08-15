"""
Backfill SEKALI-JALAN: isi histori harga SPY yang bolong di `daily_features`,
dari sebelum main.py mulai nyimpen baris SPY tiap hari.

Bukan bagian dari cron harian -- jalankan manual sekali aja, lokal atau di
Colab, abis itu bisa dihapus/diarsip.

Butuh environment variable yang sama kayak GitHub Actions secrets:
  SUPABASE_URL, SUPABASE_KEY (service_role)

Cara jalanin (lokal, folder yang sama dengan main.py/database.py/feature_engine.py):
  export SUPABASE_URL="https://pklhkbcglltayrdqeeph.supabase.co"
  export SUPABASE_KEY="service_role_key_kamu"
  pip install yfinance pandas supabase pandas_ta
  python backfill_spy.py
"""
import sys
import pandas as pd
import yfinance as yf

from feature_engine import compute_features
from database import get_client, push_features

# Tanggal entry sinyal pertama di trade_signals (cek langsung ke DB: 2026-07-23).
# Ganti kalau ternyata ada trade lebih lama yang perlu ke-cover juga.
START_DATE = "2026-07-23"


def main():
    print("Fetch histori SPY (3 tahun, biar cukup lookback buat MA200)...")
    df = yf.download("SPY", period="3y", interval="1d", auto_adjust=True, progress=False)
    if df.empty:
        print("Gagal fetch SPY dari yfinance, stop.")
        sys.exit(1)

    # Kadang yfinance balikin kolom multi-index walau cuma 1 ticker -- ratakan.
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    dates_to_backfill = [d for d in df.index if str(d.date()) >= START_DATE]
    if not dates_to_backfill:
        print(f"Nggak ada hari perdagangan >= {START_DATE} di data yang di-fetch, stop.")
        sys.exit(1)

    print(f"{len(dates_to_backfill)} hari perdagangan mau di-backfill: "
          f"{dates_to_backfill[0].date()} s/d {dates_to_backfill[-1].date()}")

    rows = []
    skipped = 0
    for d in dates_to_backfill:
        df_slice = df.loc[:d].copy()  # no-lookahead: cuma data sampai tanggal itu
        feat = compute_features("SPY", df_slice, df_slice)
        if feat is not None:
            rows.append(feat)
        else:
            skipped += 1

    print(f"{len(rows)} baris berhasil dihitung ({skipped} di-skip, kemungkinan "
          f"lookback <200 hari untuk tanggal paling awal).")

    supabase = get_client()
    push_features(supabase, rows)  # upsert on_conflict=(ticker,date), aman dijalankan berkali-kali
    print(f"Selesai. {len(rows)} baris SPY di-push/di-upsert ke daily_features.")


if __name__ == "__main__":
    main()
