-- Prevent duplicate system-generated research jobs for the same normalized source event.
-- User-requested jobs are unaffected.
create unique index if not exists uq_research_jobs_system_source_event
on public.research_jobs ((metadata->>'source_event_id'))
where metadata->>'system_generated' = 'true'
  and metadata ? 'source_event_id';
