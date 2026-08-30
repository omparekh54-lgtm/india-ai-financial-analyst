-- Trace normalized benchmark bars back to the immutable source artifact that produced them.
alter table public.benchmark_bars
  add column if not exists source_id uuid references public.sources(id) on delete set null;

create index if not exists benchmark_bars_source_id_idx
  on public.benchmark_bars(source_id)
  where source_id is not null;
