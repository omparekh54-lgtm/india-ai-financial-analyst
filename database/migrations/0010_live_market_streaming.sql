create table if not exists live_market_subscriptions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  security_id uuid not null references securities(id) on delete cascade,
  provider text not null,
  mode text not null default 'ltpc' check (mode in ('ltpc', 'full')),
  active_until timestamptz not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique(user_id, security_id, provider)
);

create index if not exists live_market_subscriptions_due_idx
  on live_market_subscriptions(provider, user_id, active_until desc);

create table if not exists user_live_quotes (
  user_id uuid not null references auth.users(id) on delete cascade,
  security_id uuid not null references securities(id) on delete cascade,
  provider text not null,
  instrument_id text not null,
  last_price numeric not null,
  close_price numeric,
  last_trade_at timestamptz,
  received_at timestamptz not null default now(),
  bid numeric,
  ask numeric,
  volume numeric,
  market_status text,
  payload jsonb not null default '{}'::jsonb,
  primary key (user_id, security_id, provider)
);

create index if not exists user_live_quotes_fresh_idx
  on user_live_quotes(user_id, provider, received_at desc);

create table if not exists broker_stream_leases (
  user_id uuid not null references auth.users(id) on delete cascade,
  provider text not null,
  worker_id text not null,
  leased_until timestamptz not null,
  heartbeat_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  primary key (user_id, provider)
);

create index if not exists broker_stream_leases_expiry_idx
  on broker_stream_leases(provider, leased_until);

alter table live_market_subscriptions enable row level security;
alter table user_live_quotes enable row level security;
alter table broker_stream_leases enable row level security;

-- Stream state is backend-only. The public API exposes only sanitized quote/status data.
revoke all on live_market_subscriptions, user_live_quotes, broker_stream_leases
from anon, authenticated;
