create table if not exists corporate_event_sources (
  event_id uuid not null references corporate_events(id) on delete cascade,
  source_id uuid not null references sources(id) on delete cascade,
  document_role text not null default 'attachment'
    check (document_role in ('announcement', 'attachment', 'xbrl', 'transcript', 'presentation', 'annual_report', 'other')),
  media_type text,
  parse_status text not null default 'pending'
    check (parse_status in ('pending', 'parsed', 'unsupported', 'failed')),
  parsed_at timestamptz,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  primary key (event_id, source_id)
);

create index if not exists corporate_event_sources_source_idx
  on corporate_event_sources(source_id, event_id);

create index if not exists corporate_event_sources_event_status_idx
  on corporate_event_sources(event_id, parse_status);

alter table corporate_event_sources enable row level security;

-- Filing-document relations and extracted chunks remain backend-only. User-visible
-- evidence is mediated through authenticated research reports and API endpoints.
revoke all on corporate_event_sources from anon, authenticated;
