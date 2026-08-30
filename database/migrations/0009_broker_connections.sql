create table if not exists broker_connections (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  provider text not null,
  encrypted_access_token text not null,
  encrypted_refresh_token text,
  token_expires_at timestamptz,
  provider_user_id text,
  provider_user_name text,
  status text not null default 'active'
    check (status in ('active', 'expired', 'revoked', 'error')),
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique(user_id, provider)
);

create index if not exists broker_connections_user_provider_idx
  on broker_connections(user_id, provider);
create index if not exists broker_connections_expiry_idx
  on broker_connections(provider, token_expires_at)
  where status = 'active';

create table if not exists broker_oauth_states (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  provider text not null,
  state_hash text not null unique,
  expires_at timestamptz not null,
  consumed_at timestamptz,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists broker_oauth_states_lookup_idx
  on broker_oauth_states(provider, state_hash, expires_at)
  where consumed_at is null;

alter table broker_connections enable row level security;
alter table broker_oauth_states enable row level security;

-- Broker credentials and OAuth state are deliberately backend-only. The API uses
-- explicit authenticated-user filters before reading/updating them. Browser clients
-- must never receive ciphertext, refresh tokens, or OAuth state rows directly.
revoke all on broker_connections, broker_oauth_states from anon, authenticated;
