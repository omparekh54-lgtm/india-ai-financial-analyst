create table if not exists ingestion_runs (
  id uuid primary key default gen_random_uuid(),
  pipeline text not null,
  scope text,
  status text not null default 'queued'
    check (status in ('queued', 'running', 'completed', 'partial', 'failed')),
  started_at timestamptz,
  completed_at timestamptz,
  checkpoint jsonb not null default '{}'::jsonb,
  stats jsonb not null default '{}'::jsonb,
  error_code text,
  error_message text,
  created_at timestamptz not null default now()
);

create index if not exists ingestion_runs_pipeline_created_idx
  on ingestion_runs(pipeline, created_at desc);

create table if not exists macro_observations (
  id uuid primary key default gen_random_uuid(),
  series_key text not null,
  observation_date date not null,
  value numeric not null,
  unit text,
  source_id uuid references sources(id) on delete set null,
  released_at timestamptz,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create unique index if not exists macro_observations_natural_key_idx
  on macro_observations(
    series_key,
    observation_date,
    coalesce(source_id, '00000000-0000-0000-0000-000000000000'::uuid)
  );

create index if not exists macro_observations_latest_idx
  on macro_observations(series_key, observation_date desc);

create table if not exists security_metrics (
  id uuid primary key default gen_random_uuid(),
  security_id uuid not null references securities(id) on delete cascade,
  metric_name text not null,
  as_of_date date not null,
  value numeric not null,
  unit text,
  source_id uuid references sources(id) on delete set null,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create unique index if not exists security_metrics_natural_key_idx
  on security_metrics(
    security_id,
    metric_name,
    as_of_date,
    coalesce(source_id, '00000000-0000-0000-0000-000000000000'::uuid)
  );

create index if not exists security_metrics_latest_idx
  on security_metrics(security_id, metric_name, as_of_date desc);

create table if not exists benchmarks (
  id uuid primary key default gen_random_uuid(),
  code text not null unique,
  name text not null,
  kind text not null check (kind in ('market', 'sector', 'industry', 'volatility')),
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists security_benchmarks (
  security_id uuid not null references securities(id) on delete cascade,
  benchmark_id uuid not null references benchmarks(id) on delete cascade,
  role text not null check (role in ('market', 'sector', 'industry')),
  created_at timestamptz not null default now(),
  primary key (security_id, role)
);

create table if not exists benchmark_bars (
  benchmark_id uuid not null references benchmarks(id) on delete cascade,
  interval text not null,
  ts timestamptz not null,
  open numeric,
  high numeric,
  low numeric,
  close numeric,
  volume numeric,
  provider text not null,
  is_adjusted boolean not null default false,
  primary key (benchmark_id, interval, ts, provider)
);

create index if not exists benchmark_bars_lookup_idx
  on benchmark_bars(benchmark_id, interval, ts desc);

insert into benchmarks (code, name, kind, metadata)
values
  ('NIFTY50', 'NIFTY 50', 'market', '{"exchange":"NSE"}'::jsonb),
  ('NIFTYBANK', 'NIFTY Bank', 'sector', '{"exchange":"NSE"}'::jsonb),
  ('NIFTYIT', 'NIFTY IT', 'sector', '{"exchange":"NSE"}'::jsonb),
  ('INDIAVIX', 'India VIX', 'volatility', '{"exchange":"NSE"}'::jsonb)
on conflict (code) do update set
  name = excluded.name,
  kind = excluded.kind,
  metadata = excluded.metadata;

alter table ingestion_runs enable row level security;
alter table macro_observations enable row level security;
alter table security_metrics enable row level security;
alter table benchmarks enable row level security;
alter table security_benchmarks enable row level security;
alter table benchmark_bars enable row level security;

revoke all on
  ingestion_runs,
  macro_observations,
  security_metrics,
  benchmarks,
  security_benchmarks,
  benchmark_bars
from anon, authenticated;
