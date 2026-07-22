"""
Strategi: pullback. Entry di level MA20 (limit order — beda dari breakout
yang stop-buy di HIGH20), baca feature abstraction yang sama dari
feature_engine.py.

Kriteria final berdasarkan 2 backtest breakdown (3 tahun, 197 ticker):
1. Kombinasi trend+momentum+RS vs trend+momentum vs trend+RS vs trend saja
   -> RS DIBUANG (menurunkan robustness: PF ex-top10 1.33 -> 1.10)
2. Isolasi murni "trend+momentum" vs "trend TANPA momentum" (mutually
   exclusive) -> KEDUANYA lolos ambang (WR>33.3%, PF ex-top10 >1):
     trend+momentum      : WR 47.4%, PF ex-top10 1.33 -> tier "kuat"
     trend tanpa momentum: WR 49.4%, PF ex-top10 1.18 -> tier "watchlist"
   (beda dari breakout, di mana score 80-85 punya median NEGATIF -- di sini
   "trend saja" masih genuine edge, cuma lebih lemah dari trend+momentum)
"""

RR_RATIO = 2
ATR_MULTIPLIER_SL = 2


def run_pullback(features):
    trend_ok = features["close"] > features["ma50"] > features["ma200"]
    momentum_ok = 40 <= features["rsi14"] <= 55

    signals = []
    if trend_ok:
        signals.append("Trend Bullish")
    if momentum_ok:
        signals.append("Pullback ke MA20")

    if trend_ok and momentum_ok:
        score, tier = 100, "kuat"
    elif trend_ok:
        score, tier = 50, "watchlist"
    else:
        score, tier = 0, "no_signal"

    entry_price = features["ma20"]
    atr_distance = ATR_MULTIPLIER_SL * (features["atr14_pct"] / 100 * entry_price)
    stop_loss = round(entry_price - atr_distance, 2)
    risk_per_share = round(entry_price - stop_loss, 2)
    take_profit = round(entry_price + risk_per_share * RR_RATIO, 2)

    return {
        "strategy": "pullback",
        "ticker": features["ticker"],
        "date": features["date"],
        "score": score,
        "signals": signals,
        "decision": {
            "tier": tier,
            "trend": "Strong" if features["close"] > features["ma200"] else "Weak",
            "entry_price_ref": entry_price,
            "stop_loss_ref": stop_loss,
            "take_profit_ref": take_profit,
        }
    }
