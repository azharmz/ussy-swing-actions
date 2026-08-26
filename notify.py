"""
Notifikasi Telegram. Kalau TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID belum di-set,
notifikasi di-skip diam-diam (bukan error) — supaya orang yang belum setup
Telegram tetap bisa jalanin pipeline tanpa notifikasi tanpa perlu ubah kode.
"""
import os
import requests


def send_telegram_message(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("Telegram belum dikonfigurasi (TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID kosong), skip notifikasi.")
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        resp = requests.post(
            url,
            data={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        )
        if not resp.ok:
            print(f"Gagal kirim Telegram: {resp.status_code} {resp.text}")
        else:
            print("Notifikasi Telegram terkirim.")
    except Exception as e:
        print(f"Error kirim Telegram: {e}")


def format_new_signals_message(new_signals, strategy_rows=None):
    """strategy_rows opsional -- kalau ada, dipakai buat label tier (misal
    "kuat-dalam"/"kuat-dangkal" pullback) di pesan. Lookup dari strategy_rows
    (sudah di memory saat run yang sama), BUKAN kolom baru di trade_signals --
    tabel itu sengaja tidak diubah skemanya untuk fitur ini. Tier lengkap tetap
    bisa dicek belakangan lewat trade_signals_full_v.snapshot_tier (join ke
    strategy_results, sudah ada dari awal)."""
    if not new_signals:
        return None

    tier_lookup = {}
    if strategy_rows:
        for r in strategy_rows:
            tier_lookup[(r["ticker"], r["strategy"])] = r["decision"].get("tier")

    by_strategy = {}
    for s in new_signals:
        by_strategy.setdefault(s["strategy"], []).append(s)

    lines = [f"*Sinyal Baru — {len(new_signals)} ticker (score=100)*", ""]
    for strategy, sigs in by_strategy.items():
        lines.append(f"_Strategi: {strategy.upper()}_")
        for s in sigs:
            tier = tier_lookup.get((s["ticker"], s["strategy"]))
            tier_label = f" [{tier}]" if tier and tier != "kuat" else ""
            lines.append(
                f"*{s['ticker']}*{tier_label}\n"
                f"Entry: `{s['entry_price']}`  SL: `{s['stop_loss']}`  TP: `{s['take_profit']}`"
            )
        lines.append("")

    lines.append("_Bukan rekomendasi finansial. Detail: uswing.anyapp.my.id_")
    return "\n".join(lines)