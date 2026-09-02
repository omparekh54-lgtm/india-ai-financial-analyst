create table if not exists security_aliases (
  id uuid primary key default gen_random_uuid(),
  security_id uuid not null references securities(id) on delete cascade,
  alias text not null,
  alias_type text not null default 'common_name',
  normalized_alias text not null,
  created_at timestamptz not null default now(),
  unique(security_id, normalized_alias)
);

create index if not exists security_aliases_lookup_idx
  on security_aliases(normalized_alias);

create table if not exists provider_instruments (
  id uuid primary key default gen_random_uuid(),
  security_id uuid not null references securities(id) on delete cascade,
  provider text not null,
  instrument_id text not null,
  exchange_segment text,
  trading_symbol text,
  metadata jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now(),
  unique(provider, instrument_id)
);

create index if not exists provider_instruments_security_idx
  on provider_instruments(security_id, provider);

create table if not exists corporate_events (
  id uuid primary key default gen_random_uuid(),
  security_id uuid not null references securities(id) on delete cascade,
  event_type text not null,
  headline text not null,
  event_at timestamptz,
  source_id uuid references sources(id) on delete set null,
  materiality numeric(5,4) check (materiality is null or (materiality >= 0 and materiality <= 1)),
  data jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists corporate_events_security_time_idx
  on corporate_events(security_id, event_at desc);

create table if not exists financial_facts (
  id uuid primary key default gen_random_uuid(),
  security_id uuid not null references securities(id) on delete cascade,
  fact_name text not null,
  period_start date,
  period_end date not null,
  period_type text not null,
  value numeric,
  unit text,
  source_id uuid references sources(id) on delete set null,
  data jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  unique(security_id, fact_name, period_end, period_type, source_id)
);

create index if not exists financial_facts_lookup_idx
  on financial_facts(security_id, fact_name, period_end desc);

create table if not exists analysis_snapshots (
  id uuid primary key default gen_random_uuid(),
  security_id uuid not null references securities(id) on delete cascade,
  job_id uuid references research_jobs(id) on delete set null,
  snapshot_type text not null default 'full_analysis',
  snapshot_at timestamptz not null default now(),
  thesis_hash text,
  metrics jsonb not null default '{}'::jsonb,
  catalysts jsonb not null default '[]'::jsonb,
  risks jsonb not null default '[]'::jsonb,
  metadata jsonb not null default '{}'::jsonb
);

create index if not exists analysis_snapshots_security_time_idx
  on analysis_snapshots(security_id, snapshot_at desc);

alter table security_aliases enable row level security;
alter table provider_instruments enable row level security;
alter table corporate_events enable row level security;
alter table financial_facts enable row level security;
alter table analysis_snapshots enable row level security;

revoke all on
  security_aliases,
  provider_instruments,
  corporate_events,
  financial_facts,
  analysis_snapshots
from anon, authenticated;
