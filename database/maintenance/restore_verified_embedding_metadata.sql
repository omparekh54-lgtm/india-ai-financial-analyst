-- Recovery for unchanged chunks reimported by the pre-fix collector.
-- Run 36772247500 attempt 2 completed at 2026-09-30T22:07:18Z with 4,607 embeddings.
-- Operator verified all vectors had 384 dimensions and the MiniLM model metadata.
-- The old upsert retained a vector ONLY when the content remained identical.
-- Do not use for later/unknown vectors, changed content, or a different embedding model.
with repaired as (
  update evidence_chunks ec
  set metadata = ec.metadata || jsonb_build_object(
    'embedding_model', 'sentence-transformers/all-MiniLM-L6-v2',
    'embedding_status', 'embedded',
    'embedding_metadata_recovered_from_run', '36772247500/attempt-2'
  )
  from sources s
  where s.id = ec.source_id and s.source_type = 'exchange_filing'
    and ec.created_at < timestamptz '2026-09-30T22:07:18Z'
    and ec.embedding is not null and extensions.vector_dims(ec.embedding) = 384
    and ec.metadata->>'embedding_model' is null
    and ec.metadata->>'evidence_kind' = 'deterministic_xbrl_fact_summary'
    and ec.metadata->>'content_sha256' = encode(sha256(convert_to(ec.content, 'UTF8')), 'hex')
  returning ec.id
)
select count(*) as repaired_model_metadata from repaired;
