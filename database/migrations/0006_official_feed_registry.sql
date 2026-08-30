create table if not exists official_data_feeds (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  provider text not null check (provider in ('NSE', 'BSE', 'RBI', 'NSDL')),
  feed_type text not null check (
    feed_type in ('exchange_disclosures', 'financial_xbrl', 'rbi_macro', 'nsdl_flows')
  ),
  source_url text not null check (source_url like 'https://%'),
  exchange text check (exchange is null or exchange in ('NSE', 'BSE')),
  identifier text,
  title text,
  parser_config jsonb not null default '{}'::jsonb,
  poll_interval_seconds integer not null default 900
    check (poll_interval_seconds between 300 and 86400),
  enabled boolean not null default true,
  etag text,
  last_modified text,
  last_started_at timestamptz,
  last_completed_at timestamptz,
  last_success_at timestamptz,
  last_error text,
  next_run_at timestamptz not null default now(),
  lease_until timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint official_feed_xbrl_identifier check (
    feed_type <> 'financial_xbrl'
    or (exchange is not null and identifier is not null and length(trim(identifier)) > 0)
  ),
  constraint official_feed_exchange_config check (
    feed_type <> 'exchange_disclosures' or exchange is not null
  )
);

create unique index if not exists official_data_feeds_identity_idx
  on official_data_feeds(
    provider,
    feed_type,
    source_url,
    coalesce(exchange, ''),
    coalesce(identifier, '')
  );

create index if not exists official_data_feeds_due_idx
  on official_data_feeds(enabled, next_run_at)
  where enabled = true;

create table if not exists official_ingestion_runs (
  id uuid primary key default gen_random_uuid(),
  feed_id uuid not null references official_data_feeds(id) on delete cascade,
  started_at timestamptz not null default now(),
  completed_at timestamptz,
  status text not null default 'running'
    check (status in ('running', 'success', 'not_modified', 'failed')),
  http_etag text,
  http_last_modified text,
  parsed_count integer,
  ingested_count integer,
  result jsonb not null default '{}'::jsonb,
  error_type text,
  error_message text
);

create index if not exists official_ingestion_runs_feed_started_idx
  on official_ingestion_runs(feed_id, started_at desc);

alter table official_data_feeds enable row level security;
alter table official_ingestion_runs enable row level security;

revoke all on official_data_feeds from anon, authenticated;
revoke all on official_ingestion_runs from anon, authenticated;
