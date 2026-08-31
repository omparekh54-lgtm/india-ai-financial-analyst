-- Prevent duplicate event-triggered research jobs for the same normalized source event and owner.
-- This supports one private watchlist refresh per user while retaining one optional system job.
drop index if exists public.uq_research_jobs_system_source_event;

create unique index if not exists uq_research_jobs_system_source_event
on public.research_jobs (
  (metadata->>'source_event_id'),
  (coalesce(requested_by::text, 'system'))
)
where metadata->>'system_generated' = 'true'
  and metadata ? 'source_event_id';
