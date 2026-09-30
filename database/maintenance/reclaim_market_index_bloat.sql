-- Operator maintenance: reclaim B-tree bloat without deleting any sourced rows.
-- Run after reviewing active ingestion jobs and recording exact row counts and sizes.
-- Rebuilds preserve the existing key definitions and uniqueness constraints.
-- Short lock timeout aborts instead of waiting behind application traffic.
-- Briefly blocks queries/writes on market_bars while each index is rebuilt.
set local lock_timeout = '5s';
set local statement_timeout = '120s';
reindex index public.market_bars_lookup_idx;
reindex index public.market_bars_pkey;
