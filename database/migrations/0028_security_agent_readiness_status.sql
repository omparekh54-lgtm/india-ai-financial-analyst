-- Prepared, per-security and per-agent readiness.
--
-- Runtime research remains fail-closed: a prepared row is an operational cache of the
-- existing readiness contract, not an override. Backend code must reject missing, stale,
-- or mismatched rule-version rows and recalculate them from source-linked inputs.

create table if not exists public.security_agent_readiness_status (
  security_id uuid not null references public.securities(id) on delete cascade,
  agent_name text not null,
  ready boolean not null,
  errors jsonb not null default '[]'::jsonb,
  warnings jsonb not null default '[]'::jsonb,
  coverage jsonb not null default '{}'::jsonb,
  blocking_agents jsonb not null default '[]'::jsonb,
  latest_market_session date,
  latest_financial_period date,
  latest_filing_at timestamptz,
  latest_earnings_at timestamptz,
  rule_version text not null,
  evaluated_at timestamptz not null,
  updated_at timestamptz not null default now(),
  primary key (security_id, agent_name),
  constraint security_agent_readiness_agent_name_nonempty
    check (length(btrim(agent_name)) > 0),
  constraint security_agent_readiness_rule_version_nonempty
    check (length(btrim(rule_version)) > 0),
  constraint security_agent_readiness_errors_array
    check (jsonb_typeof(errors) = 'array'),
  constraint security_agent_readiness_warnings_array
    check (jsonb_typeof(warnings) = 'array'),
  constraint security_agent_readiness_coverage_object
    check (jsonb_typeof(coverage) = 'object'),
  constraint security_agent_readiness_blocking_agents_array
    check (jsonb_typeof(blocking_agents) = 'array')
);

create index if not exists security_agent_readiness_ready_idx
  on public.security_agent_readiness_status(agent_name, ready, evaluated_at desc);

create index if not exists security_agent_readiness_security_eval_idx
  on public.security_agent_readiness_status(security_id, evaluated_at desc);

alter table public.security_agent_readiness_status enable row level security;
revoke all on public.security_agent_readiness_status from anon, authenticated;

drop policy if exists security_agent_readiness_backend_only on public.security_agent_readiness_status;
create policy security_agent_readiness_backend_only
  on public.security_agent_readiness_status for all to anon, authenticated
  using (false) with check (false);
