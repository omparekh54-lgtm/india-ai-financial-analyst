create index if not exists evidence_chunks_source_id_idx
  on evidence_chunks(source_id);

create index if not exists evidence_chunks_embedding_hnsw_idx
  on evidence_chunks
  using hnsw (embedding vector_cosine_ops)
  where embedding is not null;
