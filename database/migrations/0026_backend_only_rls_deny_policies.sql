-- Explicit backend-only RLS policies for tables that are intentionally not
-- exposed to direct anon/authenticated Data API access.
--
-- The previous no-policy state already denied direct RLS reads/writes, but
-- Supabase advisors flag "RLS enabled no policy". These deny-all policies make
-- the product intent auditable without granting any client access.

do $$
declare
  target_table text;
begin
  foreach target_table in array ARRAY[
    'benchmark_bars',
    'benchmarks',
    'broker_connections',
    'broker_oauth_states',
    'broker_stream_leases',
    'commercial_source_approvals',
    'corporate_event_sources',
    'corporate_events',
    'evidence_chunks',
    'financial_facts',
    'ingestion_runs',
    'live_market_subscriptions',
    'macro_observations',
    'market_bars',
    'official_data_feeds',
    'official_ingestion_runs',
    'provider_instruments',
    'security_aliases',
    'security_benchmarks',
    'security_metrics',
    'sources',
    'user_live_quotes'
  ]
  loop
    execute format('alter table public.%I enable row level security', target_table);
    execute format('revoke all on table public.%I from anon, authenticated', target_table);

    if not exists (
      select 1
      from pg_policy
      where polrelid = format('public.%I', target_table)::regclass
        and polname = 'backend_only_deny_all'
    ) then
      execute format(
        'create policy backend_only_deny_all on public.%I for all to anon, authenticated using (false) with check (false)',
        target_table
      );
    end if;
  end loop;
end $$;
