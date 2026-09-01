-- Phase 22: historical research calibration. Evaluations are deterministic outcomes computed only
-- after the required future source-linked trading bars exist. Client users may read only outcomes
-- tied to their own research snapshots; all writes remain backend-only.

create table if not exists public.research_evaluations (
  id uuid primary key default gen_random_uuid(),
  snapshot_id uuid not null references public.analysis_snapshots(id) on delete cascade,
  security_id uuid not null references public.securities(id) on delete cascade,
  horizon_sessions integer not null check (horizon_sessions in (20, 60, 120)),
  start_bar_date date not null,
  end_bar_date date not null,
  start_price numeric not null check (start_price > 0),
  end_price numeric not null check (end_price > 0),
  stock_return_pct numeric not null,
  benchmark_code text,
  benchmark_start_price numeric check (benchmark_start_price is null or benchmark_start_price > 0),
  benchmark_end_price numeric check (benchmark_end_price is null or benchmark_end_price > 0),
  benchmark_return_pct numeric,
  excess_return_pct numeric,
  thesis_hash text,
  confidence jsonb not null default '{}'::jsonb,
  metadata jsonb not null default '{}'::jsonb,
  evaluated_at timestamptz not null default now(),
  constraint research_evaluations_dates_order check (end_bar_date > start_bar_date),
  unique (snapshot_id, horizon_sessions)
);

create index if not exists research_evaluations_security_horizon_idx
  on public.research_evaluations(security_id, horizon_sessions, evaluated_at desc);
create index if not exists research_evaluations_snapshot_idx
  on public.research_evaluations(snapshot_id);

alter table public.research_evaluations enable row level security;
revoke all on public.research_evaluations from anon, authenticated;
grant select on public.research_evaluations to authenticated;

drop policy if exists research_evaluations_owner_read on public.research_evaluations;
create policy research_evaluations_owner_read
  on public.research_evaluations
  for select
  to authenticated
  using (
    exists (
      select 1
      from public.analysis_snapshots snap
      join public.research_jobs job on job.id=snap.job_id
      where snap.id=research_evaluations.snapshot_id
        and job.requested_by=(select auth.uid())
    )
  );
