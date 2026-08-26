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

# H2 (reverse-engineering backtest 2018-2026, n=8.382 trade pullback): dist_ma20_pct
# rendah saat sinyal -> PF lebih tinggi (p=0.0006, lolos Bonferroni, replikasi di
# test). TAPI regime-conditional -- robust & signifikan di bull (90% sample, tahan
# sampai ex-top20), arah TERBALIK di bear (sample kecil), tidak signifikan di
# sideways. Threshold FIXED dari median backtest historis (bukan dihitung ulang
# dari kondisi live) supaya persis kriteria yang divalidasi, bukan definisi baru
# yang belum diuji.
DIST_MA20_THRESHOLD = -0.48


def run_pullback(features, market_regime="non_bull"):
    """market_regime: "bull" (SPY Close > MA200) atau "non_bull" (selain itu).
    Dipakai buat gate confidence sub-tier dist_ma20 -- di luar bull, arah H2
    belum terbukti (malah kebalik di bear), jadi jangan diklaim dalam/dangkal."""
    trend_ok = features["close"] > features["ma50"] > features["ma200"]
    momentum_ok = 40 <= features["rsi14"] <= 55

    signals = []
    if trend_ok:
        signals.append("Trend Bullish")
    if momentum_ok:
        signals.append("Pullback ke MA20")

    if trend_ok and momentum_ok:
        score = 100
        if market_regime == "bull":
            if features["dist_ma20_pct"] < DIST_MA20_THRESHOLD:
                tier = "kuat-dalam"
                signals.append("Pullback dalam (dist_ma20 rendah)")
            else:
                tier = "kuat-dangkal"
        else:
            # Regime bukan bull -> H2 belum tervalidasi di sini, jangan bedain.
            tier = "kuat"
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
            "market_regime": market_regime,
            "distance_ma20_pct": features["dist_ma20_pct"],
            "entry_price_ref": entry_price,
            "stop_loss_ref": stop_loss,
            "take_profit_ref": take_profit,
        }
    }