-- Production performance hardening identified by the Supabase database advisor.
-- This preserves the existing fail-closed security model while avoiding per-row
-- auth.uid() evaluation and covering foreign keys used by broker/live-market joins.

create index if not exists broker_oauth_states_user_id_idx
  on public.broker_oauth_states(user_id);

create index if not exists live_market_subscriptions_security_id_idx
  on public.live_market_subscriptions(security_id);

create index if not exists user_live_quotes_security_id_idx
  on public.user_live_quotes(security_id);

-- Cache auth.uid() once per statement via an initplan instead of evaluating it
-- for every candidate row. Ownership semantics are unchanged.

drop policy if exists research_jobs_owner_read on public.research_jobs;
create policy research_jobs_owner_read
  on public.research_jobs
  for select
  to authenticated
  using (requested_by = (select auth.uid()));

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
        and job.requested_by = (select auth.uid())
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
        and job.requested_by = (select auth.uid())
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
        and job.requested_by = (select auth.uid())
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
        and job.requested_by = (select auth.uid())
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
        and job.requested_by = (select auth.uid())
    )
  );
