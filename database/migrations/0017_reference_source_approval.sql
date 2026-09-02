-- Generic reference imports must be explicitly production-approved.
-- Official ingestion paths use dedicated source types and are governed separately.
-- This constraint prevents direct SQL or future scripts from bypassing importer-level
-- provenance validation for reference_* source rows.

alter table public.sources
  add constraint sources_reference_production_approved_chk
  check (
    source_type not like 'reference_%'
    or lower(coalesce(metadata->>'production_approved', 'false'))
      in ('true', '1', 'yes', 'y', 'on')
  ) not valid;

alter table public.sources
  validate constraint sources_reference_production_approved_chk;
