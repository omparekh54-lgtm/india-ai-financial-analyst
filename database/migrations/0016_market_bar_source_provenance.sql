-- Trace normalized security market bars back to the immutable source artifact that produced them.
alter table public.market_bars
  add column if not exists source_id uuid references public.sources(id) on delete set null;

create index if not exists market_bars_source_id_idx
  on public.market_bars(source_id)
  where source_id is not null;
