alter table corporate_events
  add column if not exists fingerprint text;

create unique index if not exists corporate_events_fingerprint_idx
  on corporate_events(security_id, fingerprint)
  where fingerprint is not null;

create unique index if not exists sources_security_uri_published_idx
  on sources(
    security_id,
    source_uri,
    coalesce(published_at, '1970-01-01 00:00:00+00'::timestamptz)
  );

create unique index if not exists financial_facts_natural_key_idx
  on financial_facts(
    security_id,
    fact_name,
    period_end,
    period_type,
    coalesce(source_id, '00000000-0000-0000-0000-000000000000'::uuid)
  );

create index if not exists sources_type_retrieved_idx
  on sources(source_type, retrieved_at desc);
