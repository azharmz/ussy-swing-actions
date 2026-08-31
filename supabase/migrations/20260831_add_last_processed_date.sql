-- Makes trade signal lifecycle processing idempotent and replayable.
-- Existing rows are initialized to the market date most likely represented by
-- their latest successful update. New rows set this field to signal_date.

alter table public.trade_signals
    add column if not exists last_processed_date date;

update public.trade_signals
set last_processed_date = coalesce(
    (updated_at at time zone 'America/New_York')::date,
    signal_date
)
where last_processed_date is null;

alter table public.trade_signals
    alter column last_processed_date set not null;

comment on column public.trade_signals.last_processed_date is
    'Latest market candle applied to this signal; prevents same-day double processing and enables replay after failed jobs.';
