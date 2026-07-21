"""
Signal engine — implementasi live dari logic yang sudah divalidasi di
notebook backtest (backtest_score_breakdown.py): breakout window HIGH20,
entry validity 3 hari bursa, holding period 10 hari bursa, SL=entry-2xATR14,
TP=entry+2x(entry-SL). Sinyal cuma dibuat untuk score=100 (satu-satunya
level yang terbukti punya edge di backtest).

Urutan proses tiap run (penting, jangan dibalik):
1. update_active_signals()  -> proses sinyal yang SUDAH ada dulu, pakai
   High/Low HARI INI. Ini yang membuat sinyal baru hari ini otomatis tidak
   ikut dicek hari ini juga (sama seperti backtest: entry window mulai
   besok, bukan hari sinyal muncul).
2. generate_new_signals()   -> baru setelah itu, buat sinyal baru untuk
   ticker score=100 yang belum punya sinyal aktif.

Anti-duplikat dijaga dua lapis: dicek di sini (tickers_with_active) DAN
di-enforce keras oleh partial unique index di database
(trade_signals_one_active_per_ticker) sebagai pengaman terakhir.
"""
import pandas as pd

ENTRY_WINDOW_DAYS = 3
HOLDING_PERIOD_DAYS = 10
HIGH_N = 20
ATR_MULTIPLIER_SL = 2
RR_RATIO = 2


def _high_n(df, n=HIGH_N):
    """20-day high TIDAK termasuk hari ini — level breakout dihitung dari
    data SEBELUM hari berjalan, konsisten dengan HIGH20.shift(1) di backtest."""
    if len(df) < n + 1:
        return None
    return df["High"].iloc[-(n + 1):-1].max()


def update_active_signals(supabase, price_data):
    """Proses sinyal pending/entered yang sudah ada, pakai OHLC hari ini.
    Return: set ticker yang masih/baru py di-exclude dari pembuatan sinyal baru hari ini."""
    active = supabase.table("trade_signals").select("*").in_(
        "status", ["pending", "entered"]
    ).execute().data

    tickers_with_active = set()

    for sig in active:
        tickers_with_active.add(sig["ticker"])
        ticker = sig["ticker"]

        if ticker not in price_data:
            print(f"  [signal] {ticker}: gagal fetch hari ini, status dibiarkan apa adanya")
            continue

        df = price_data[ticker]
        today = df.iloc[-1]
        today_date = str(df.index[-1].date())

        if today_date == sig["signal_date"]:
            continue  # sinyal baru dibuat hari ini, entry window mulai besok

        if sig["status"] == "pending":
            if today["Low"] <= sig["stop_loss"]:
                supabase.table("trade_signals").update({
                    "status": "missed", "missed_reason": "sl_hit_before_entry"
                }).eq("id", sig["id"]).execute()
            elif today["High"] >= sig["entry_price"]:
                entry_actual = round(max(float(today["Open"]), sig["entry_price"]), 2)
                supabase.table("trade_signals").update({
                    "status": "entered", "entry_price_actual": entry_actual,
                    "entry_date": today_date, "days_in_status": 0
                }).eq("id", sig["id"]).execute()
            else:
                new_days = sig["days_in_status"] + 1
                if new_days >= ENTRY_WINDOW_DAYS:
                    supabase.table("trade_signals").update({
                        "status": "missed", "missed_reason": "expired"
                    }).eq("id", sig["id"]).execute()
                else:
                    supabase.table("trade_signals").update({
                        "days_in_status": new_days
                    }).eq("id", sig["id"]).execute()

        elif sig["status"] == "entered":
            entry_actual = sig["entry_price_actual"]
            if today["Low"] <= sig["stop_loss"]:
                exit_price = sig["stop_loss"]
                pnl = round((exit_price - entry_actual) / entry_actual * 100, 2)
                supabase.table("trade_signals").update({
                    "status": "sl_hit", "exit_price": exit_price,
                    "exit_date": today_date, "pnl_pct": pnl
                }).eq("id", sig["id"]).execute()
            elif today["High"] >= sig["take_profit"]:
                exit_price = sig["take_profit"]
                pnl = round((exit_price - entry_actual) / entry_actual * 100, 2)
                supabase.table("trade_signals").update({
                    "status": "tp_hit", "exit_price": exit_price,
                    "exit_date": today_date, "pnl_pct": pnl
                }).eq("id", sig["id"]).execute()
            else:
                new_days = sig["days_in_status"] + 1
                if new_days >= HOLDING_PERIOD_DAYS:
                    exit_price = round(float(today["Close"]), 2)
                    pnl = round((exit_price - entry_actual) / entry_actual * 100, 2)
                    supabase.table("trade_signals").update({
                        "status": "closed_timeout", "exit_price": exit_price,
                        "exit_date": today_date, "pnl_pct": pnl
                    }).eq("id", sig["id"]).execute()
                else:
                    supabase.table("trade_signals").update({
                        "days_in_status": new_days
                    }).eq("id", sig["id"]).execute()

    return tickers_with_active


def generate_new_signals(supabase, price_data, strategy_rows, tickers_with_active):
    """Bikin sinyal pending baru — HANYA untuk score=100 (satu-satunya level
    yang terbukti punya edge di backtest), dan HANYA kalau ticker itu belum
    punya sinyal aktif."""
    new_rows = []

    for r in strategy_rows:
        if r["score"] < 100:
            continue
        ticker = r["ticker"]
        if ticker in tickers_with_active:
            continue
        if ticker not in price_data:
            continue

        df = price_data[ticker]
        entry_price = _high_n(df)
        if entry_price is None or pd.isna(entry_price):
            continue
        if "ATR14" not in df.columns or pd.isna(df["ATR14"].iloc[-1]):
            continue

        atr = float(df["ATR14"].iloc[-1])
        entry_price = round(float(entry_price), 2)
        stop_loss = round(entry_price - ATR_MULTIPLIER_SL * atr, 2)
        risk = entry_price - stop_loss
        take_profit = round(entry_price + RR_RATIO * risk, 2)

        new_rows.append({
            "ticker": ticker,
            "strategy": r["strategy"],
            "signal_date": r["date"],
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "status": "pending",
            "days_in_status": 0,
        })

    if new_rows:
        supabase.table("trade_signals").upsert(
            new_rows, on_conflict="ticker,signal_date,strategy"
        ).execute()

    return new_rows


def process_signals(supabase, price_data, strategy_rows):
    tickers_with_active = update_active_signals(supabase, price_data)
    new_signals = generate_new_signals(supabase, price_data, strategy_rows, tickers_with_active)

    print(f"Signal engine: {len(new_signals)} sinyal baru (score=100), "
          f"{len(tickers_with_active)} ticker sudah punya sinyal aktif (di-skip)")
    return new_signals
