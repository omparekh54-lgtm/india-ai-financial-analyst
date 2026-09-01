-- Private user watchlists. Research/event ingestion remains backend-owned; users only manage
-- their own watchlist records. Watchlist event refresh is opt-in per security.

create table if not exists public.watchlists (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  name text not null default 'Default',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint watchlists_name_nonempty check (length(btrim(name)) between 1 and 80),
  unique (user_id, name)
);

create table if not exists public.watchlist_items (
  watchlist_id uuid not null references public.watchlists(id) on delete cascade,
  security_id uuid not null references public.securities(id) on delete cascade,
  notes text,
  event_research_enabled boolean not null default true,
  added_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (watchlist_id, security_id),
  constraint watchlist_items_notes_length check (notes is null or length(notes) <= 1000)
);

create index if not exists watchlists_user_updated_idx
  on public.watchlists(user_id, updated_at desc);

create index if not exists watchlist_items_security_event_idx
  on public.watchlist_items(security_id, event_research_enabled)
  where event_research_enabled = true;

alter table public.watchlists enable row level security;
alter table public.watchlist_items enable row level security;

grant select, insert, update, delete on public.watchlists to authenticated;
grant select, insert, update, delete on public.watchlist_items to authenticated;

drop policy if exists watchlists_owner_all on public.watchlists;
create policy watchlists_owner_all
  on public.watchlists
  for all
  to authenticated
  using (user_id = (select auth.uid()))
  with check (user_id = (select auth.uid()));

drop policy if exists watchlist_items_owner_all on public.watchlist_items;
create policy watchlist_items_owner_all
  on public.watchlist_items
  for all
  to authenticated
  using (
    exists (
      select 1
      from public.watchlists w
      where w.id = watchlist_items.watchlist_id
        and w.user_id = (select auth.uid())
    )
  )
  with check (
    exists (
      select 1
      from public.watchlists w
      where w.id = watchlist_items.watchlist_id
        and w.user_id = (select auth.uid())
    )
  );
