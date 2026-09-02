-- Phase 24: usage and commercial launch controls. This adds quota accounting and explicit
-- commercial-source approval records; it does not create billing, licensing approval or paid
-- provider activation by itself.

create table if not exists public.user_usage_daily (
  user_id uuid not null references auth.users(id) on delete cascade,
  usage_date date not null default current_date,
  research_jobs integer not null default 0 check (research_jobs >= 0),
  deep_research_jobs integer not null default 0 check (deep_research_jobs >= 0),
  event_research_jobs integer not null default 0 check (event_research_jobs >= 0),
  updated_at timestamptz not null default now(),
  primary key (user_id, usage_date)
);

alter table public.user_usage_daily enable row level security;
revoke all on public.user_usage_daily from anon, authenticated;
grant select on public.user_usage_daily to authenticated;

drop policy if exists user_usage_daily_owner_read on public.user_usage_daily;
create policy user_usage_daily_owner_read
  on public.user_usage_daily
  for select
  to authenticated
  using (user_id=(select auth.uid()));

create table if not exists public.commercial_source_approvals (
  id uuid primary key default gen_random_uuid(),
  provider text not null,
  source_scope text not null,
  use_case text not null check (use_case in ('internal_research','user_display','redistribution')),
  status text not null default 'pending' check (status in ('pending','approved','rejected','expired')),
  approval_reference text,
  approved_at timestamptz,
  expires_at timestamptz,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint commercial_source_scope_nonempty check (length(btrim(source_scope)) between 1 and 200),
  constraint commercial_source_approval_reference check (
    status <> 'approved' or length(btrim(coalesce(approval_reference,''))) > 0
  ),
  unique (provider, source_scope, use_case)
);

create index if not exists commercial_source_approvals_status_idx
  on public.commercial_source_approvals(status, use_case, expires_at);

alter table public.commercial_source_approvals enable row level security;
revoke all on public.commercial_source_approvals from anon, authenticated;
