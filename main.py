from fetch import fetch_prices
from feature_engine import compute_features
from strategy_breakout import run_breakout
from database import get_client, push_features, push_strategy_results

# Universe halal x XTB (197 ticker, sektor financial sudah di-exclude manual)
UNIVERSE = [
    "AAPL", "ABT", "ACAD", "ACM", "ADBE", "ADPT", "ADSK", "AEM",
    "AKAM", "ALB", "ALC", "ALGN", "ALLE", "ALNY", "AMAT", "AMD",
    "AME", "AMPL", "ANET", "ANF", "AOS", "APD", "ASAN", "ASML",
    "ASND", "AVGO", "AZN", "AZO", "BB", "BBY", "BDX", "BHP",
    "BIIB", "BIRD", "BOX", "BRZE", "BSX", "CAH", "CDNS", "CDW",
    "CELH", "CF", "CHD", "CHRW", "CL", "CLX", "CNI", "CNQ",
    "CPNG", "CPRI", "CRL", "CRM", "CROX", "CRSR", "CRWD", "CSCO",
    "CSX", "CTAS", "CVX", "DASH", "DD", "DDOG", "DECK", "DHI",
    "DHR", "DOCU", "DOV", "DXCM", "ECL", "EL", "EMR", "ENPH",
    "ENTG", "EOG", "EPAM", "EQIX", "EXPD", "FFIV", "FIGS", "FIVN",
    "FIZZ", "FRSH", "FSLR", "FSLY", "FTNT", "GDDY", "GILD", "GLW",
    "GPC", "GPRO", "GRMN", "GSK", "GTLB", "HAL", "HD", "HNST",
    "HSY", "HUBS", "IDXX", "ILMN", "INCY", "ISRG", "IT", "ITW",
    "JBHT", "JCI", "JMIA", "JNJ", "KEYS", "KLAC", "KLTR", "KMB",
    "KO", "LEN", "LEVI", "LIN", "LLY", "LOGI", "LOW", "LRCX",
    "LULU", "MCHP", "MCK", "MDB", "MDT", "MKC", "MMM", "MNST",
    "MRK", "MRVL", "MSI", "MU", "NEGG", "NKE", "NOW", "NTAP",
    "NUE", "NVDA", "NVS", "ODFL", "OKTA", "OTIS", "PANW", "PATH",
    "PG", "PHM", "PLUG", "PPG", "PWR", "QCOM", "RBLX", "RL",
    "RMD", "ROK", "ROST", "SAP", "SBUX", "SEDG", "SHOP", "SLB",
    "SNOW", "SNPS", "SNY", "STM", "STX", "SU", "SWKS", "TDUP",
    "TEAM", "TECH", "TER", "TJX", "TPR", "TSCO", "TSLA", "TSM",
    "TTD", "TWLO", "TXG", "TXN", "UBER", "ULTA", "UMC", "UNP",
    "UPS", "VLO", "VRSN", "VRTX", "WDC", "WIX", "WM", "WMS",
    "WSM", "XOM", "XYL", "ZS", "ZTS",
]


def main():
    supabase = get_client()
    all_tickers = UNIVERSE + ["SPY"]
    price_data = fetch_prices(all_tickers)

    if "SPY" not in price_data:
        print("SPY gagal di-fetch, hentikan proses (RS butuh SPY)")
        raise SystemExit(1)

    spy_df = price_data["SPY"]
    feature_rows = []
    strategy_rows = []

    for ticker in UNIVERSE:
        if ticker not in price_data:
            continue
        feat = compute_features(ticker, price_data[ticker], spy_df)
        if feat is None:
            continue
        feature_rows.append(feat)
        strategy_rows.append(run_breakout(feat))

    print(f"Berhasil proses {len(feature_rows)}/{len(UNIVERSE)} ticker")

    push_features(supabase, feature_rows)
    push_strategy_results(supabase, strategy_rows)

    print("Selesai push ke Supabase")

    kuat = [r for r in strategy_rows if r["decision"]["tier"] == "kuat"]
    watchlist = [r for r in strategy_rows if r["decision"]["tier"] == "watchlist"]
    print(f"Kuat: {len(kuat)} | Watchlist: {len(watchlist)}")

    # Kalau proses jauh lebih sedikit dari universe, kemungkinan ada masalah
    # fetch (rate-limit dsb) — biarkan job gagal biar kelihatan di GitHub Actions,
    # bukan diam-diam push data yang cacat.
    if len(feature_rows) < len(UNIVERSE) * 0.8:
        print("WARNING: <80% ticker berhasil diproses, cek log fetch di atas.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
