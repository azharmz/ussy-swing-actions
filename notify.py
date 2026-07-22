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


def format_new_signals_message(new_signals):
    if not new_signals:
        return None

    by_strategy = {}
    for s in new_signals:
        by_strategy.setdefault(s["strategy"], []).append(s)

    lines = [f"*Sinyal Baru — {len(new_signals)} ticker (score=100)*", ""]
    for strategy, sigs in by_strategy.items():
        lines.append(f"_Strategi: {strategy.upper()}_")
        for s in sigs:
            lines.append(
                f"*{s['ticker']}*\n"
                f"Entry: `{s['entry_price']}`  SL: `{s['stop_loss']}`  TP: `{s['take_profit']}`"
            )
        lines.append("")

    lines.append("_Bukan rekomendasi finansial. Detail: uswing.anyapp.my.id_")
    return "\n".join(lines)
