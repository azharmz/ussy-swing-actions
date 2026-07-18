"""
Koneksi Supabase & push helper. Kredensial dari environment variable
(diisi lewat GitHub Actions secrets), bukan hardcoded.
"""
import os
from supabase import create_client, Client


def get_client() -> Client:
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_KEY"]  # service_role — wajib, RLS aktif di kedua tabel
    return create_client(url, key)


def push_features(supabase: Client, feature_rows):
    if not feature_rows:
        return
    supabase.table("daily_features").upsert(
        feature_rows, on_conflict="ticker,date"
    ).execute()


def push_strategy_results(supabase: Client, strategy_rows):
    if not strategy_rows:
        return
    supabase.table("strategy_results").upsert(
        strategy_rows, on_conflict="ticker,date,strategy"
    ).execute()
