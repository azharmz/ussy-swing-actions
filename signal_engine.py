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
buat rentang seluruh masa sinyal. MFE/MAE terpisah dan baru dimulai saat entry,
supaya pergerakan ketika masih pending tidak mencemari excursion trade.

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

CATATAN (v5): mfe_pct/mae_pct menyimpan maximum favorable/adverse excursion
sejak entry. Karena data hanya OHLC harian, candle entry memakai seluruh rentang
High/Low candle tersebut (urutan intraday tidak tersedia); hasil dijepit terhadap
entry supaya MFE tidak negatif dan MAE tidak positif.
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


def _updated_mfe_mae(sig, today, entry_actual, initialize=False):
    """Update percentage excursions relative to the actual fill price."""
    high_pct = (float(today["High"]) - float(entry_actual)) / float(entry_actual) * 100
    low_pct = (float(today["Low"]) - float(entry_actual)) / float(entry_actual) * 100
    high_pct = round(max(0.0, high_pct), 2)
    low_pct = round(min(0.0, low_pct), 2)

    prev_mfe = None if initialize else sig.get("mfe_pct")
    prev_mae = None if initialize else sig.get("mae_pct")
    # Legacy trades entered before v5 cannot be reconstructed correctly from
    # lowest/highest_price because those fields include the pending window.
    # Keep them NULL permanently instead of presenting partial data as if it
    # covered the full trade.
    if not initialize and (prev_mfe is None or prev_mae is None):
        return None, None
    mfe = high_pct if prev_mfe is None else max(float(prev_mfe), high_pct)
    mae = low_pct if prev_mae is None else min(float(prev_mae), low_pct)
    return round(mfe, 2), round(mae, 2)


def _excursions_from_frame(df, entry_date, exit_date, entry_actual):
    """Reconstruct MFE/MAE from entry through exit/latest available bar."""
    if not entry_date or entry_actual is None or float(entry_actual) <= 0:
        return None
    start = pd.Timestamp(entry_date).date()
    end = pd.Timestamp(exit_date).date() if exit_date else None
    dates = df.index.date
    mask = dates >= start
    if end is not None:
        mask &= dates <= end
    window = df.loc[mask]
    if window.empty or window[["High", "Low"]].isna().any().any():
        return None
    entry = float(entry_actual)
    mfe = round(max(0.0, (float(window["High"].max()) - entry) / entry * 100), 2)
    mae = round(min(0.0, (float(window["Low"].min()) - entry) / entry * 100), 2)
    return mfe, mae


def backfill_missing_excursions(supabase, price_data):
    """One-time/lazy backfill using OHLC already fetched by the daily run.

    No additional market-data request is made. Trades outside the available
    one-year price window or tickers no longer in the universe remain NULL and
    can later be filled from the long-history R2 archive.
    """
    try:
        rows = supabase.table("trade_signals").select(
            "id,ticker,strategy,entry_date,exit_date,entry_price_actual,mfe_pct,mae_pct"
        ).execute().data
    except Exception as e:
        print(f"  [signal] Backfill MFE/MAE dilewati (kolom belum tersedia?): {e}")
        return 0

    candidates = [
        row for row in rows
        if row.get("entry_date") and row.get("entry_price_actual") is not None
        and (row.get("mfe_pct") is None or row.get("mae_pct") is None)
    ]
    updated = 0
    unavailable = 0
    for sig in candidates:
        df = price_data.get(sig["ticker"])
        excursion = None if df is None else _excursions_from_frame(
            df.sort_index(), sig["entry_date"], sig.get("exit_date"),
            sig["entry_price_actual"],
        )
        if excursion is None:
            unavailable += 1
            continue
        mfe, mae = excursion
        context = f"{sig['ticker']}/{sig['strategy']}/backfill-excursion"
        if _safe_update(supabase, sig, {"mfe_pct": mfe, "mae_pct": mae}, context):
            updated += 1

    if candidates:
        print(f"  [signal] Backfill MFE/MAE: {updated}/{len(candidates)} trade terisi; "
              f"{unavailable} belum punya OHLC lengkap di window 1 tahun.")
    return updated


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
            mfe, mae = _updated_mfe_mae(sig, today, entry_actual, initialize=True)
            return _safe_update(supabase, sig, {
                **common,
                "status": "sl_hit", "entry_price_actual": entry_actual,
                "entry_date": today_date, "exit_price": exit_price,
                "exit_date": today_date, "pnl_pct": pnl,
                "mfe_pct": mfe, "mae_pct": mae,
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
            mfe, mae = _updated_mfe_mae(sig, today, entry_actual, initialize=True)
            return _safe_update(supabase, sig, {
                **common,
                "status": "entered", "entry_price_actual": entry_actual,
                "entry_date": today_date, "days_in_status": 0,
                "mark_price": round(float(today["Close"]), 2),
                "mfe_pct": mfe, "mae_pct": mae,
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
        mfe, mae = _updated_mfe_mae(sig, today, entry_actual)
        excursion = {} if mfe is None or mae is None else {"mfe_pct": mfe, "mae_pct": mae}
        if today["Low"] <= sig["stop_loss"]:
            exit_price = _gap_aware_stop_price(today, sig["stop_loss"])
            pnl = round((exit_price - entry_actual) / entry_actual * 100, 2)
            return _safe_update(supabase, sig, {
                **common,
                "status": "sl_hit", "exit_price": exit_price,
                "exit_date": today_date, "pnl_pct": pnl,
                **excursion,
                "days_in_status": new_days,
            }, context)

        if today["High"] >= sig["take_profit"]:
            exit_price = sig["take_profit"]
            pnl = round((exit_price - entry_actual) / entry_actual * 100, 2)
            return _safe_update(supabase, sig, {
                **common,
                "status": "tp_hit", "exit_price": exit_price,
                "exit_date": today_date, "pnl_pct": pnl,
                **excursion,
                "days_in_status": new_days,
            }, context)

        if new_days >= HOLDING_PERIOD_DAYS:
            exit_price = round(float(today["Close"]), 2)
            pnl = round((exit_price - entry_actual) / entry_actual * 100, 2)
            return _safe_update(supabase, sig, {
                **common,
                "status": "closed_timeout", "exit_price": exit_price,
                "exit_date": today_date, "pnl_pct": pnl,
                **excursion,
                "days_in_status": new_days,
            }, context)

        return _safe_update(supabase, sig, {
            **common,
            "days_in_status": new_days,
            "mark_price": round(float(today["Close"]), 2),
            **excursion,
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
            "mfe_pct": None,
            "mae_pct": None,
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
    backfill_missing_excursions(supabase, price_data)
    tickers_with_active = update_active_signals(supabase, price_data)
    new_signals = generate_new_signals(supabase, price_data, strategy_rows, tickers_with_active)

    print(f"Signal engine: {len(new_signals)} sinyal baru (score=100), "
          f"{len(tickers_with_active)} (ticker,strategi) sudah punya sinyal aktif (di-skip)")
    return new_signals
