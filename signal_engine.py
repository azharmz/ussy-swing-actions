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

CATATAN (v3): fix gap kecil -- di update yang mengubah STATUS (transisi ke
missed/entered/sl_hit/tp_hit/closed_timeout), days_in_status yang sudah
di-increment untuk hari itu sekarang IKUT disertakan di payload (sebelumnya
kelewat di beberapa tempat: sl_hit_before_entry, missed/expired, sl_hit,
tp_hit, closed_timeout -- cuma cabang "masih lanjut, belum transisi" yang
sudah benar dari awal). Efeknya: days_in_status yang tersimpan buat baris
yang sudah resolved/missed sekarang akurat sampai hari terakhir, bukan
ketinggalan 1 hari. Nggak ngubah threshold ENTRY_WINDOW_DAYS/
HOLDING_PERIOD_DAYS itu sendiri, cuma ngebenerin apa yang KE-SIMPAN.

CATATAN (v4): lifecycle menyimpan last_processed_date. Rerun pada candle yang
sama sekarang idempotent, dan kalau job harian gagal maka semua candle yang
terlewat diproses berurutan pada run berikutnya. Candle pending yang menyentuh
entry+SL sekaligus dihitung konservatif sebagai trade sl_hit (bukan dikeluarkan
dari performa sebagai missed), dan stop yang di-gap memakai min(Open, stop).
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
        # Keep the in-memory state in sync. This matters when a failed/delayed
        # workflow has more than one unprocessed candle to replay in one run.
        sig.update(payload)
        return True
    except Exception as e:
        print(f"  [signal] GAGAL update {context} (id={sig.get('id')}): {e}")
        return False


def _bars_after(df, last_processed_date):
    """Return unprocessed bars in chronological order.

    last_processed_date is persisted per signal, making manual reruns on the
    same market date idempotent and allowing a later run to replay candles
    missed by a failed GitHub Actions job.
    """
    if last_processed_date:
        return df[df.index.date > pd.Timestamp(last_processed_date).date()]
    return df


def _gap_aware_stop_price(today, stop_loss):
    """A sell-stop cannot fill above the open after a gap through the stop."""
    return round(min(float(today["Open"]), float(stop_loss)), 2)


def _process_signal_bar(supabase, sig, today, today_date, direction, context):
    """Process exactly one previously unseen OHLC bar.

    Returns False when persistence fails; the caller must then stop replaying
    later bars so lifecycle events cannot be applied out of order.
    """
    new_low, new_high = _updated_low_high(sig, today)
    new_days = sig["days_in_status"] + 1
    common = {
        "last_processed_date": today_date,
        "lowest_price": new_low,
        "highest_price": new_high,
    }

    if sig["status"] == "pending":
        entry_triggered = (
            today["High"] >= sig["entry_price"] if direction == "above"
            else today["Low"] <= sig["entry_price"]
        )
        stop_touched = today["Low"] <= sig["stop_loss"]

        # Daily OHLC cannot reveal which level was touched first. When both
        # entry and stop occur in one candle, count the conservative outcome
        # as a trade and a stop, rather than excluding a potential loss as
        # missed/sl_hit_before_entry.
        if entry_triggered and stop_touched:
            entry_actual = round(
                max(float(today["Open"]), sig["entry_price"])
                if direction == "above"
                else min(float(today["Open"]), sig["entry_price"]),
                2,
            )
            exit_price = _gap_aware_stop_price(today, sig["stop_loss"])
            # A pullback limit can fill below the stored stop on a gap down.
            # Do not manufacture a positive "stop" exit in that case.
            exit_price = min(exit_price, entry_actual)
            pnl = round((exit_price - entry_actual) / entry_actual * 100, 2)
            return _safe_update(supabase, sig, {
                **common,
                "status": "sl_hit", "entry_price_actual": entry_actual,
                "entry_date": today_date, "exit_price": exit_price,
                "exit_date": today_date, "pnl_pct": pnl,
                "days_in_status": 0,
            }, context)

        if stop_touched:
            return _safe_update(supabase, sig, {
                **common,
                "status": "missed", "missed_reason": "sl_hit_before_entry",
                "days_in_status": new_days,
            }, context)

        if entry_triggered:
            entry_actual = round(
                max(float(today["Open"]), sig["entry_price"])
                if direction == "above"
                else min(float(today["Open"]), sig["entry_price"]),
                2,
            )
            return _safe_update(supabase, sig, {
                **common,
                "status": "entered", "entry_price_actual": entry_actual,
                "entry_date": today_date, "days_in_status": 0,
                "mark_price": round(float(today["Close"]), 2),
            }, context)

        if new_days >= ENTRY_WINDOW_DAYS:
            return _safe_update(supabase, sig, {
                **common,
                "status": "missed", "missed_reason": "expired",
                "days_in_status": new_days,
            }, context)

        return _safe_update(supabase, sig, {
            **common,
            "days_in_status": new_days,
            "mark_price": round(float(today["Close"]), 2),
        }, context)

    if sig["status"] == "entered":
        entry_actual = sig["entry_price_actual"]
        if today["Low"] <= sig["stop_loss"]:
            exit_price = _gap_aware_stop_price(today, sig["stop_loss"])
            pnl = round((exit_price - entry_actual) / entry_actual * 100, 2)
            return _safe_update(supabase, sig, {
                **common,
                "status": "sl_hit", "exit_price": exit_price,
                "exit_date": today_date, "pnl_pct": pnl,
                "days_in_status": new_days,
            }, context)

        if today["High"] >= sig["take_profit"]:
            exit_price = sig["take_profit"]
            pnl = round((exit_price - entry_actual) / entry_actual * 100, 2)
            return _safe_update(supabase, sig, {
                **common,
                "status": "tp_hit", "exit_price": exit_price,
                "exit_date": today_date, "pnl_pct": pnl,
                "days_in_status": new_days,
            }, context)

        if new_days >= HOLDING_PERIOD_DAYS:
            exit_price = round(float(today["Close"]), 2)
            pnl = round((exit_price - entry_actual) / entry_actual * 100, 2)
            return _safe_update(supabase, sig, {
                **common,
                "status": "closed_timeout", "exit_price": exit_price,
                "exit_date": today_date, "pnl_pct": pnl,
                "days_in_status": new_days,
            }, context)

        return _safe_update(supabase, sig, {
            **common,
            "days_in_status": new_days,
            "mark_price": round(float(today["Close"]), 2),
        }, context)

    return True


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

        df = price_data[ticker].sort_index()
        last_processed = sig.get("last_processed_date") or sig["signal_date"]
        unseen = _bars_after(df, last_processed)

        for bar_date, today in unseen.iterrows():
            today_date = str(bar_date.date())
            ok = _process_signal_bar(
                supabase, sig, today, today_date, direction, context
            )
            if not ok:
                fail_count += 1
                break
            if sig["status"] not in ("pending", "entered"):
                break

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
            "last_processed_date": r["date"],
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
