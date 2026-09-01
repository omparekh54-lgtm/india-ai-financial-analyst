-- Supabase performance advisor recommends wrapping auth.uid() in a scalar SELECT so
-- PostgreSQL can evaluate it once per statement instead of once per row.

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
