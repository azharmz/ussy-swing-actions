import csv

import pandas as pd

from fetch import fetch_prices
from feature_engine import compute_features
from strategy_breakout import run_breakout
from strategy_pullback import run_pullback
from database import get_client, push_features, push_strategy_results
from signal_engine import process_signals
from notify import send_telegram_message, format_new_signals_message

UNIVERSE_CSV_PATH = "universe.csv"


def load_universe(csv_path=UNIVERSE_CSV_PATH):
    """Universe halal x XTB, dibaca langsung dari universe.csv (export
    Musaffa) tiap run -- BUKAN hardcoded list di kode. Update universe jadi
    cuma "timpa universe.csv, commit, push" -- run cron berikutnya otomatis
    pakai daftar terbaru, nggak perlu edit main.py sama sekali.
    Kolom yang dipakai cuma "Ticker"; Sector/Industry (kalau ada di CSV)
    diabaikan di sini -- itu dipakai di sisi lain (tabel ticker_sector di
    Supabase, untuk exposure snapshot per sektor di frontend), bukan di sini.
    """
    with open(csv_path, newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows or "Ticker" not in rows[0]:
        raise SystemExit(f"{csv_path} kosong atau kolom 'Ticker' nggak ketemu, cek formatnya.")
    tickers = sorted(set(r["Ticker"].strip() for r in rows if r["Ticker"].strip()))
    return tickers


def main():
    supabase = get_client()
    UNIVERSE = load_universe()
    print(f"Universe: {len(UNIVERSE)} ticker (dari {UNIVERSE_CSV_PATH})")
    all_tickers = UNIVERSE + ["SPY"]
    price_data = fetch_prices(all_tickers)

    if "SPY" not in price_data:
        print("SPY gagal di-fetch, hentikan proses (RS butuh SPY)")
        raise SystemExit(1)

    spy_df = price_data["SPY"]
    feature_rows = []
    strategy_rows = []

    # SPY sendiri ikut disimpan sebagai baris daily_features (ticker="SPY"),
    # pakai compute_features() yang sama persis dengan ticker universe --
    # cuma dipakai buat benchmark chart "vs SPY" di frontend (paper trading),
    # BUKAN bagian dari screening/strategi manapun. relative_strength_spy
    # hasilnya 0 (dibanding diri sendiri) -- nggak masalah, kolom itu nggak
    # dipakai untuk baris SPY ini.
    spy_feat = compute_features("SPY", spy_df, spy_df)
    if spy_feat is not None:
        feature_rows.append(spy_feat)
    else:
        print("WARNING: SPY gagal dihitung fiturnya (data <200 hari?), "
              "benchmark chart di frontend nggak akan ke-update hari ini.")

    # Regime flag sederhana (SPY Close vs MA200) -- reuse MA200 yang sudah
    # dihitung compute_features() di atas (mutasi in-place ke spy_df), bukan
    # dihitung ulang. Dipakai buat gate confidence sub-tier dist_ma20 pullback
    # (H2 cuma tervalidasi & robust di bull regime, riset Notebook 3 -- di
    # bear arahnya kebalik, di sideways tidak signifikan). Default konservatif
    # "non_bull" kalau MA200 belum kehitung (data kurang).
    if "MA200" in spy_df.columns and pd.notna(spy_df["MA200"].iloc[-1]):
        spy_close_today = float(spy_df["Close"].iloc[-1])
        spy_ma200_today = float(spy_df["MA200"].iloc[-1])
        market_regime = "bull" if spy_close_today > spy_ma200_today else "non_bull"
        print(f"Market regime hari ini: {market_regime} (SPY {spy_close_today:.2f} vs MA200 {spy_ma200_today:.2f})")
    else:
        market_regime = "non_bull"
        print("Market regime: MA200 SPY belum kehitung (data <200 hari?), default non_bull (konservatif)")

    for ticker in UNIVERSE:
        if ticker not in price_data:
            continue
        feat = compute_features(ticker, price_data[ticker], spy_df)
        if feat is None:
            continue
        feature_rows.append(feat)
        strategy_rows.append(run_breakout(feat))
        strategy_rows.append(run_pullback(feat, market_regime))

    print(f"Berhasil proses {len(feature_rows)}/{len(UNIVERSE) + 1} ticker (termasuk SPY)")

    push_features(supabase, feature_rows)
    push_strategy_results(supabase, strategy_rows)

    print("Selesai push ke Supabase")

    new_signals = process_signals(supabase, price_data, strategy_rows)

    msg = format_new_signals_message(new_signals, strategy_rows)
    if msg:
        send_telegram_message(msg)

    for strategy_name in ("breakout", "pullback"):
        rows_s = [r for r in strategy_rows if r["strategy"] == strategy_name]
        # startswith("kuat") biar "kuat", "kuat-dalam", "kuat-dangkal" kehitung semua
        # sebagai "sinyal kuat" di ringkasan -- cuma soal sub-tier, bukan beda level.
        kuat = [r for r in rows_s if r["decision"]["tier"].startswith("kuat")]
        print(f"{strategy_name}: {len(kuat)} sinyal kuat dari {len(rows_s)} ticker")

    # Kalau proses jauh lebih sedikit dari universe, kemungkinan ada masalah
    # fetch (rate-limit dsb) — biarkan job gagal biar kelihatan di GitHub Actions,
    # bukan diam-diam push data yang cacat.
    if len(feature_rows) < len(UNIVERSE) * 0.8:
        print("WARNING: <80% ticker berhasil diproses, cek log fetch di atas.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()