"""
Strategy: breakout. Baca feature abstraction (dict dari feature_engine),
tidak menghitung indikator mentah sendiri.

Batasan tier dari hasil backtest 3 tahun (197 ticker):
score >=80 -> excess return konsisten positif di semua horizon (5/10/20d)
score 40-79 -> tidak reliably beda dari baseline
"""
TIER_ACTIONABLE = 80
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
