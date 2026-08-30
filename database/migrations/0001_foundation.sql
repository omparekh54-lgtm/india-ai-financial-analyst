create extension if not exists pgcrypto;
create extension if not exists vector;

create table if not exists securities (
  id uuid primary key default gen_random_uuid(),
  legal_name text not null,
  nse_symbol text,
  bse_code text,
  isin text unique,
  currency text not null default 'INR',
  sector text,
  industry text,
  primary_exchange text,
  fno_eligible boolean not null default false,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create unique index if not exists securities_nse_symbol_idx on securities(nse_symbol) where nse_symbol is not null;
create unique index if not exists securities_bse_code_idx on securities(bse_code) where bse_code is not null;

create table if not exists research_jobs (
  id uuid primary key default gen_random_uuid(),
  security_id uuid references securities(id),
  query text not null,
  status text not null default 'queued',
  mode text not null default 'full_analysis',
  requested_by uuid,
  started_at timestamptz,
  completed_at timestamptz,
  created_at timestamptz not null default now(),
  metadata jsonb not null default '{}'::jsonb
);

create table if not exists sources (
  id uuid primary key default gen_random_uuid(),
  security_id uuid references securities(id),
  source_type text not null,
  source_uri text not null,
  title text,
  published_at timestamptz,
  retrieved_at timestamptz not null default now(),
  freshness text not null default 'unknown',
  checksum text,
  metadata jsonb not null default '{}'::jsonb
);

create index if not exists sources_security_published_idx on sources(security_id, published_at desc);

create table if not exists evidence_chunks (
  id uuid primary key default gen_random_uuid(),
  source_id uuid not null references sources(id) on delete cascade,
  chunk_index integer not null,
  page_number integer,
  section text,
  content text not null,
  embedding vector(384),
  metadata jsonb not null default '{}'::jsonb,
  unique(source_id, chunk_index)
);

create table if not exists agent_runs (
  id uuid primary key default gen_random_uuid(),
  job_id uuid not null references research_jobs(id) on delete cascade,
  agent_name text not null,
  status text not null default 'queued',
  provider text,
  model text,
  started_at timestamptz,
  completed_at timestamptz,
  latency_ms integer,
  input_tokens integer,
  output_tokens integer,
  warnings jsonb not null default '[]'::jsonb,
  errors jsonb not null default '[]'::jsonb,
  metadata jsonb not null default '{}'::jsonb
);

create index if not exists agent_runs_job_idx on agent_runs(job_id, agent_name);

create table if not exists claims (
  id uuid primary key default gen_random_uuid(),
  job_id uuid not null references research_jobs(id) on delete cascade,
  agent_run_id uuid references agent_runs(id) on delete set null,
  claim_type text not null,
  statement text not null,
  confidence numeric(5,4) not null check (confidence >= 0 and confidence <= 1),
  validation_status text not null default 'pending',
  data jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists claim_evidence (
  claim_id uuid not null references claims(id) on delete cascade,
  evidence_chunk_id uuid not null references evidence_chunks(id) on delete cascade,
  primary key (claim_id, evidence_chunk_id)
);

create table if not exists market_bars (
  security_id uuid not null references securities(id) on delete cascade,
  interval text not null,
  ts timestamptz not null,
  open numeric,
  high numeric,
  low numeric,
  close numeric,
  volume numeric,
  provider text not null,
  is_adjusted boolean not null default false,
  primary key (security_id, interval, ts, provider)
);

create index if not exists market_bars_lookup_idx on market_bars(security_id, interval, ts desc);

create table if not exists research_reports (
  id uuid primary key default gen_random_uuid(),
  job_id uuid not null unique references research_jobs(id) on delete cascade,
  executive_summary text,
  report_json jsonb not null default '{}'::jsonb,
  data_confidence numeric(5,4),
  thesis_confidence numeric(5,4),
  valuation_confidence numeric(5,4),
  catalyst_confidence numeric(5,4),
  created_at timestamptz not null default now()
);
