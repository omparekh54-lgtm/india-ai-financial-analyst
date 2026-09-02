create unique index if not exists sources_global_uri_published_idx
  on sources(
    coalesce(security_id, '00000000-0000-0000-0000-000000000000'::uuid),
    source_uri,
    coalesce(published_at, '1970-01-01 00:00:00+00'::timestamptz)
  );

create index if not exists macro_observations_series_date_idx
  on macro_observations(series_key, observation_date desc);
