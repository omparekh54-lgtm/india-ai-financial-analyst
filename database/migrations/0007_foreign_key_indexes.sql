drop index if exists macro_observations_series_date_idx;

create index if not exists analysis_snapshots_job_id_idx
  on analysis_snapshots(job_id);

create index if not exists claim_evidence_chunk_id_idx
  on claim_evidence(evidence_chunk_id);

create index if not exists claims_agent_run_id_idx
  on claims(agent_run_id);

create index if not exists claims_job_id_idx
  on claims(job_id);

create index if not exists corporate_events_source_id_idx
  on corporate_events(source_id);

create index if not exists financial_facts_source_id_idx
  on financial_facts(source_id);

create index if not exists macro_observations_source_id_idx
  on macro_observations(source_id);

create index if not exists research_jobs_security_id_idx
  on research_jobs(security_id);

create index if not exists security_benchmarks_benchmark_id_idx
  on security_benchmarks(benchmark_id);

create index if not exists security_metrics_source_id_idx
  on security_metrics(source_id);
