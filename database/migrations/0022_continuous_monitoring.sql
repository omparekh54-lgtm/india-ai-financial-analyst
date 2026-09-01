-- Phase 19: continuous monitoring and thesis-delta persistence.
-- Reconcile the historical analysis_snapshots table into the checked-in migration chain, then add
-- private owner-scoped monitoring alerts. Snapshots/alerts are written only by the trusted backend.

create table if not exists public.analysis_snapshots (
  id uuid primary key default gen_random_uuid(),
  security_id uuid not null references public.securities(id) on delete cascade,
  job_id uuid references public.research_jobs(id) on delete set null,
  snapshot_type text not null,
  snapshot_at timestamptz not null default now(),
  thesis_hash text,
  metrics jsonb not null default '{}'::jsonb,
  catalysts jsonb not null default '[]'::jsonb,
  risks jsonb not null default '[]'::jsonb,
  metadata jsonb not null default '{}'::jsonb
);

create index if not exists analysis_snapshots_security_time_idx
  on public.analysis_snapshots(security_id, snapshot_at desc);
create index if not exists analysis_snapshots_job_id_idx
  on public.analysis_snapshots(job_id);

alter table public.analysis_snapshots enable row level security;
revoke all on public.analysis_snapshots from anon, authenticated;
grant select on public.analysis_snapshots to authenticated;

drop policy if exists analysis_snapshots_owner_read on public.analysis_snapshots;
create policy analysis_snapshots_owner_read
  on public.analysis_snapshots
  for select
  to authenticated
  using (
    exists (
      select 1 from public.research_jobs job
      where job.id = analysis_snapshots.job_id
        and job.requested_by = (select auth.uid())
    )
  );

create table if not exists public.monitoring_alerts (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  security_id uuid not null references public.securities(id) on delete cascade,
  source_snapshot_id uuid not null references public.analysis_snapshots(id) on delete cascade,
  prior_snapshot_id uuid not null references public.analysis_snapshots(id) on delete cascade,
  job_id uuid references public.research_jobs(id) on delete set null,
  severity text not null check (severity in ('info', 'material', 'high')),
  summary text not null,
  delta jsonb not null,
  read_at timestamptz,
  created_at timestamptz not null default now(),
  constraint monitoring_alerts_summary_nonempty check (length(btrim(summary)) between 1 and 500),
  unique (user_id, source_snapshot_id)
);

create index if not exists monitoring_alerts_user_created_idx
  on public.monitoring_alerts(user_id, created_at desc);
create index if not exists monitoring_alerts_security_created_idx
  on public.monitoring_alerts(security_id, created_at desc);
create index if not exists monitoring_alerts_user_unread_idx
  on public.monitoring_alerts(user_id, created_at desc)
  where read_at is null;

alter table public.monitoring_alerts enable row level security;
revoke all on public.monitoring_alerts from anon, authenticated;
grant select on public.monitoring_alerts to authenticated;
grant update (read_at) on public.monitoring_alerts to authenticated;

drop policy if exists monitoring_alerts_owner_read on public.monitoring_alerts;
create policy monitoring_alerts_owner_read
  on public.monitoring_alerts
  for select
  to authenticated
  using (user_id = (select auth.uid()));

drop policy if exists monitoring_alerts_owner_mark_read on public.monitoring_alerts;
create policy monitoring_alerts_owner_mark_read
  on public.monitoring_alerts
  for update
  to authenticated
  using (user_id = (select auth.uid()))
  with check (user_id = (select auth.uid()));
