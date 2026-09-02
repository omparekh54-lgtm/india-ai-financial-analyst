-- User ownership for research artifacts. Shared ingestion/reference data remains backend-only.

do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'research_jobs_requested_by_fkey'
      and conrelid = 'public.research_jobs'::regclass
  ) then
    alter table public.research_jobs
      add constraint research_jobs_requested_by_fkey
      foreign key (requested_by) references auth.users(id) on delete cascade;
  end if;
end
$$;

create index if not exists research_jobs_requested_by_created_idx
  on public.research_jobs(requested_by, created_at desc)
  where requested_by is not null;

-- Authenticated users may read only their own research artifacts. Writes continue
-- through the trusted API/database role, which keeps orchestration internals private.
grant select on public.securities to authenticated;
grant select on public.research_jobs to authenticated;
grant select on public.agent_runs to authenticated;
grant select on public.claims to authenticated;
grant select on public.claim_evidence to authenticated;
grant select on public.research_reports to authenticated;
grant select on public.analysis_snapshots to authenticated;

drop policy if exists securities_authenticated_read on public.securities;
create policy securities_authenticated_read
  on public.securities
  for select
  to authenticated
  using (true);

drop policy if exists research_jobs_owner_read on public.research_jobs;
create policy research_jobs_owner_read
  on public.research_jobs
  for select
  to authenticated
  using (requested_by = auth.uid());

drop policy if exists agent_runs_owner_read on public.agent_runs;
create policy agent_runs_owner_read
  on public.agent_runs
  for select
  to authenticated
  using (
    exists (
      select 1
      from public.research_jobs job
      where job.id = agent_runs.job_id
        and job.requested_by = auth.uid()
    )
  );

drop policy if exists claims_owner_read on public.claims;
create policy claims_owner_read
  on public.claims
  for select
  to authenticated
  using (
    exists (
      select 1
      from public.research_jobs job
      where job.id = claims.job_id
        and job.requested_by = auth.uid()
    )
  );

drop policy if exists claim_evidence_owner_read on public.claim_evidence;
create policy claim_evidence_owner_read
  on public.claim_evidence
  for select
  to authenticated
  using (
    exists (
      select 1
      from public.claims claim
      join public.research_jobs job on job.id = claim.job_id
      where claim.id = claim_evidence.claim_id
        and job.requested_by = auth.uid()
    )
  );

drop policy if exists research_reports_owner_read on public.research_reports;
create policy research_reports_owner_read
  on public.research_reports
  for select
  to authenticated
  using (
    exists (
      select 1
      from public.research_jobs job
      where job.id = research_reports.job_id
        and job.requested_by = auth.uid()
    )
  );

drop policy if exists analysis_snapshots_owner_read on public.analysis_snapshots;
create policy analysis_snapshots_owner_read
  on public.analysis_snapshots
  for select
  to authenticated
  using (
    exists (
      select 1
      from public.research_jobs job
      where job.id = analysis_snapshots.job_id
        and job.requested_by = auth.uid()
    )
  );
