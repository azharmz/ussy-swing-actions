"""
Signal engine — implementasi live dari logic yang sudah divalidasi di
notebook backtest. Mendukung MULTI-STRATEGI (breakout & pullback), masing-
masing punya arah entry sendiri:
  - breakout: stop-buy, entry di ATAS harga sekarang (HIGH20), tunggu tembus
  - pullback: limit-buy, entry di BAWAH harga sekarang (MA20), tunggu turun

Sinyal cuma dibuat untuk score=100 di kedua strategi (satu-satunya level
yang terbukti punya edge di backtest masing-masing).

Urutan proses tiap run (penting, jangan dibalik):
1. update_active_signals()  -> proses sinyal yang SUDAH ada dulu, pakai
   High/Low HARI INI. Ini membuat sinyal baru hari ini otomatis tidak
   ikut dicek hari ini juga (sama seperti backtest: entry window mulai
   besok, bukan hari sinyal muncul).
2. generate_new_signals()   -> baru setelah itu, buat sinyal baru.

Anti-duplikat dijaga PER (ticker, strategi) — bukan per ticker doang, jadi
breakout dan pullback boleh sama-sama aktif di ticker yang sama secara
bersamaan (dua hipotesis independen). Dijaga dua lapis: dicek di sini
(tickers_with_active sebagai set of (ticker, strategy)) DAN di-enforce
keras oleh partial unique index di database
(trade_signals_one_active_per_ticker, key: ticker+strategy).

mark_price, lowest_price, highest_price di-update tiap hari selama status
pending/entered -- mark_price buat floating PnL di frontend, lowest/highest
buat lihat seberapa jauh harga sempat bergerak selama sinyal dipantau.

CATATAN (v2): tiap panggilan .update().execute() sekarang dibungkus
try/except + logging eksplisit (ticker, strategi, id, error asli). Ini
respons dari investigasi ~68% baris trade_signals punya lowest_price/
highest_price NULL padahal code path-nya selalu nyertain field itu --
dugaan kuat ada update yang gagal diam-diam (kemungkinan NaN dari yfinance
kebawa ke payload JSON dan ditolak Supabase), tapi sebelumnya nggak
kelihatan sama sekali di log run manapun karena nggak ada penanganan
error di level ini. Sekarang kalau ini kejadian lagi, bakal muncul jelas
di log GitHub Actions -- ticker/strategi mana, dan pesan error aslinya.
"""
import pandas as pd

ENTRY_WINDOW_DAYS = 3
HOLDING_PERIOD_DAYS = 10
HIGH_N = 20
ATR_MULTIPLIER_SL = 2
RR_RATIO = 2

# Arah entry per strategi -- menentukan cara cek entry window & cara isi harga.
STRATEGY_DIRECTION = {
    "breakout": "above",
    "pullback": "below",
}


def _high_n(df, n=HIGH_N):
    """20-day high TIDAK termasuk hari ini — level breakout dihitung dari
    data SEBELUM hari berjalan, konsisten dengan HIGH20.shift(1) di backtest."""
    if len(df) < n + 1:
        return None
    return df["High"].iloc[-(n + 1):-1].max()


def _entry_level(df, strategy):
    """Level entry per strategi. breakout -> 20-day high (exclude hari ini).
    pullback -> MA20 hari ini (sudah termasuk hari ini, sama seperti backtest
    yang pakai row['MA20'] langsung, bukan di-shift)."""
    if strategy == "breakout":
        return _high_n(df)
    elif strategy == "pullback":
        if "MA20" not in df.columns or len(df) == 0:
            return None
        return df["MA20"].iloc[-1]
    return None


def _updated_low_high(sig, today):
    """Gabungkan lowest/highest yang sudah tersimpan dengan Low/High hari ini."""
    today_low = round(float(today["Low"]), 2)
    today_high = round(float(today["High"]), 2)
    prev_low = sig.get("lowest_price")
    prev_high = sig.get("highest_price")
    new_low = today_low if prev_low is None else min(float(prev_low), today_low)
    new_high = today_high if prev_high is None else max(float(prev_high), today_high)
    return new_low, new_high


def _safe_update(supabase, sig, payload, context):
    """Wrapper .update().execute() dengan try/except + logging eksplisit.
    Return True kalau sukses, False kalau gagal (caller boleh lanjut ke
    sinyal berikutnya, jangan sampai 1 update gagal bikin seluruh run mati).
    Juga validasi payload dulu -- kalau ada NaN yang nyelip (misal dari
    yfinance), block SEBELUM dikirim ke Supabase, bukan nunggu ditolak
    server, biar pesan error-nya jelas nunjuk ke akar masalahnya."""
    bad_keys = [k for k, v in payload.items() if isinstance(v, float) and pd.isna(v)]
    if bad_keys:
        print(f"  [signal] GAGAL update {context} (id={sig.get('id')}): "
              f"payload mengandung NaN di kolom {bad_keys} -- kemungkinan data "
              f"Low/High/Close dari yfinance NaN hari ini, di-skip biar nggak "
              f"ngerusak baris. Sinyal dibiarkan apa adanya, dicoba lagi run berikutnya.")
        return False
    try:
        supabase.table("trade_signals").update(payload).eq("id", sig["id"]).execute()
        return True
    except Exception as e:
        print(f"  [signal] GAGAL update {context} (id={sig.get('id')}): {e}")
        return False


def update_active_signals(supabase, price_data):
    """Proses sinyal pending/entered yang sudah ada, pakai OHLC hari ini.
    Return: set (ticker, strategy) yang di-exclude dari pembuatan sinyal baru hari ini."""
    active = supabase.table("trade_signals").select("*").in_(
        "status", ["pending", "entered"]
    ).execute().data

    tickers_with_active = set()
    fail_count = 0

    for sig in active:
        ticker = sig["ticker"]
        strategy = sig["strategy"]
        tickers_with_active.add((ticker, strategy))
        direction = STRATEGY_DIRECTION.get(strategy, "above")
        context = f"{ticker}/{strategy}"

        if ticker not in price_data:
            print(f"  [signal] {ticker}/{strategy}: gagal fetch hari ini, status dibiarkan apa adanya")
            continue

        df = price_data[ticker]
        today = df.iloc[-1]
        today_date = str(df.index[-1].date())

        if today_date == sig["signal_date"]:
            continue  # sinyal baru dibuat hari ini, entry window mulai besok

        new_low, new_high = _updated_low_high(sig, today)

        if sig["status"] == "pending":
            if today["Low"] <= sig["stop_loss"]:
                ok = _safe_update(supabase, sig, {
                    "status": "missed", "missed_reason": "sl_hit_before_entry",
                    "lowest_price": new_low, "highest_price": new_high,
                }, context)
                if not ok: fail_count += 1
                continue

            entry_triggered = (
                today["High"] >= sig["entry_price"] if direction == "above"
                else today["Low"] <= sig["entry_price"]
            )

            if entry_triggered:
                if direction == "above":
                    entry_actual = round(max(float(today["Open"]), sig["entry_price"]), 2)
                else:
                    entry_actual = round(min(float(today["Open"]), sig["entry_price"]), 2)
                ok = _safe_update(supabase, sig, {
                    "status": "entered", "entry_price_actual": entry_actual,
                    "entry_date": today_date, "days_in_status": 0,
                    "mark_price": round(float(today["Close"]), 2),
                    "lowest_price": new_low, "highest_price": new_high,
                }, context)
                if not ok: fail_count += 1
            else:
                new_days = sig["days_in_status"] + 1
                if new_days >= ENTRY_WINDOW_DAYS:
                    ok = _safe_update(supabase, sig, {
                        "status": "missed", "missed_reason": "expired",
                        "lowest_price": new_low, "highest_price": new_high,
                    }, context)
                else:
                    ok = _safe_update(supabase, sig, {
                        "days_in_status": new_days,
                        "mark_price": round(float(today["Close"]), 2),
                        "lowest_price": new_low, "highest_price": new_high,
                    }, context)
                if not ok: fail_count += 1

        elif sig["status"] == "entered":
            entry_actual = sig["entry_price_actual"]
            if today["Low"] <= sig["stop_loss"]:
                exit_price = sig["stop_loss"]
                pnl = round((exit_price - entry_actual) / entry_actual * 100, 2)
                ok = _safe_update(supabase, sig, {
                    "status": "sl_hit", "exit_price": exit_price,
                    "exit_date": today_date, "pnl_pct": pnl,
                    "lowest_price": new_low, "highest_price": new_high,
                }, context)
                if not ok: fail_count += 1
            elif today["High"] >= sig["take_profit"]:
                exit_price = sig["take_profit"]
                pnl = round((exit_price - entry_actual) / entry_actual * 100, 2)
                ok = _safe_update(supabase, sig, {
                    "status": "tp_hit", "exit_price": exit_price,
                    "exit_date": today_date, "pnl_pct": pnl,
                    "lowest_price": new_low, "highest_price": new_high,
                }, context)
                if not ok: fail_count += 1
            else:
                new_days = sig["days_in_status"] + 1
                if new_days >= HOLDING_PERIOD_DAYS:
                    exit_price = round(float(today["Close"]), 2)
                    pnl = round((exit_price - entry_actual) / entry_actual * 100, 2)
                    ok = _safe_update(supabase, sig, {
                        "status": "closed_timeout", "exit_price": exit_price,
                        "exit_date": today_date, "pnl_pct": pnl,
                        "lowest_price": new_low, "highest_price": new_high,
                    }, context)
                else:
                    ok = _safe_update(supabase, sig, {
                        "days_in_status": new_days,
                        "mark_price": round(float(today["Close"]), 2),
                        "lowest_price": new_low, "highest_price": new_high,
                    }, context)
                if not ok: fail_count += 1

    if fail_count:
        print(f"  [signal] WARNING: {fail_count} update trade_signals gagal hari ini "
              f"(lihat baris 'GAGAL update' di atas buat detail per ticker).")

    return tickers_with_active


def generate_new_signals(supabase, price_data, strategy_rows, tickers_with_active):
    """Bikin sinyal pending baru — HANYA untuk score=100 (level yang terbukti
    punya edge di backtest masing-masing strategi), dan HANYA kalau
    (ticker, strategi) itu belum punya sinyal aktif."""
    new_rows = []
    skipped_nan = []

    for r in strategy_rows:
        if r["score"] < 100:
            continue
        ticker = r["ticker"]
        strategy = r["strategy"]

        if (ticker, strategy) in tickers_with_active:
            continue
        if ticker not in price_data:
            continue

        df = price_data[ticker]
        entry_price = _entry_level(df, strategy)
        if entry_price is None or pd.isna(entry_price):
            continue
        if "ATR14" not in df.columns or pd.isna(df["ATR14"].iloc[-1]):
            continue

        atr = float(df["ATR14"].iloc[-1])
        entry_price = round(float(entry_price), 2)
        stop_loss = round(entry_price - ATR_MULTIPLIER_SL * atr, 2)
        risk = entry_price - stop_loss
        take_profit = round(entry_price + RR_RATIO * risk, 2)

        today = df.iloc[-1]
        row = {
            "ticker": ticker,
            "strategy": strategy,
            "signal_date": r["date"],
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "status": "pending",
            "days_in_status": 0,
            "mark_price": round(float(today["Close"]), 2),
            "lowest_price": round(float(today["Low"]), 2),
            "highest_price": round(float(today["High"]), 2),
        }

        # Validasi NaN sebelum masuk batch upsert -- sinyal baru yang datanya
        # cacat lebih baik di-skip (coba lagi besok) daripada dipaksa masuk
        # dengan lowest_price/highest_price kosong dari awal.
        bad_keys = [k for k, v in row.items() if isinstance(v, float) and pd.isna(v)]
        if bad_keys:
            skipped_nan.append((ticker, strategy, bad_keys))
            continue

        new_rows.append(row)

    if skipped_nan:
        for ticker, strategy, bad_keys in skipped_nan:
            print(f"  [signal] Sinyal baru {ticker}/{strategy} di-skip: kolom {bad_keys} NaN "
                  f"(kemungkinan data yfinance hari ini cacat untuk ticker ini).")

    if new_rows:
        try:
            supabase.table("trade_signals").upsert(
                new_rows, on_conflict="ticker,signal_date,strategy"
            ).execute()
        except Exception as e:
            print(f"  [signal] GAGAL upsert {len(new_rows)} sinyal baru: {e}")
            print(f"  [signal] Ticker yang gagal ke-push: {[r['ticker'] + '/' + r['strategy'] for r in new_rows]}")
            return []

    return new_rows


def process_signals(supabase, price_data, strategy_rows):
    tickers_with_active = update_active_signals(supabase, price_data)
    new_signals = generate_new_signals(supabase, price_data, strategy_rows, tickers_with_active)

    print(f"Signal engine: {len(new_signals)} sinyal baru (score=100), "
          f"{len(tickers_with_active)} (ticker,strategi) sudah punya sinyal aktif (di-skip)")
    return new_signals