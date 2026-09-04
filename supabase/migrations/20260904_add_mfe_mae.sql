-- Trade excursions measured from the actual entry fill.
-- Historical rows remain NULL: lowest/highest_price previously included the
-- pending window, so using them to backfill would produce invalid MFE/MAE.

alter table public.trade_signals
    add column if not exists mfe_pct numeric,
    add column if not exists mae_pct numeric;

comment on column public.trade_signals.mfe_pct is
    'Maximum favorable excursion (%) since actual entry; NULL before entry or when legacy history cannot be reconstructed.';
comment on column public.trade_signals.mae_pct is
    'Maximum adverse excursion (%) since actual entry; zero or negative; NULL before entry or for legacy history.';

-- The existing full view uses an explicit column list. Publish the two new
-- fields through a narrow read-only companion view and merge by id in the UI.
create or replace view public.trade_signal_excursions_v as
select id, mfe_pct, mae_pct
from public.trade_signals;

grant select on public.trade_signal_excursions_v to anon, authenticated;

