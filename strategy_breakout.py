"""
Strategy: breakout. Baca feature abstraction (dict dari feature_engine),
tidak menghitung indikator mentah sendiri.

Angka docstring lama (WR 59.8%, PF 1.85) berasal dari iterasi strategi yang
sudah stale. Rekonstruksi lifecycle 2018-2026 yang tervalidasi ke data live
memberi WR 53.7%, PF agregat 1.39, dan PF ex-top10 1.29 (n=1.279). Edge sangat
terkonsentrasi dan rapuh; strategi dipertahankan apa adanya untuk observasi.

Score 80-85 tetap watchlist, bukan actionable. Hanya score=100 yang dibuatkan
sinyal oleh signal_engine.
"""
TIER_ACTIONABLE = 100
TIER_WATCHLIST = 40


def classify_tier(score):
    if score >= TIER_ACTIONABLE:
        return "kuat"
    elif score >= TIER_WATCHLIST:
        return "watchlist"
    return "no_signal"


def run_breakout(features):
    score = 0
    signals = []

    if features["close"] > features["ma50"] > features["ma200"]:
        score += 40
        signals.append("Trend Bullish")

    if 45 <= features["rsi14"] <= 70:
        score += 25
        signals.append("Momentum Sehat")

    if features["relative_volume"] and features["relative_volume"] > 1.5:
        score += 20
        signals.append("Volume Meningkat")

    if features["relative_strength_spy"] > 5:
        score += 15
        signals.append("Outperform SPY")

    return {
        "strategy": "breakout",
        "ticker": features["ticker"],
        "date": features["date"],
        "score": score,
        "signals": signals,
        "decision": {
            "tier": classify_tier(score),
            "trend": "Strong" if features["close"] > features["ma200"] else "Weak",
            "atr_stop": round(features["close"] - 2 * (features["atr14_pct"] / 100 * features["close"]), 2),
            "distance_ma20_pct": features["dist_ma20_pct"],
        }
    }
