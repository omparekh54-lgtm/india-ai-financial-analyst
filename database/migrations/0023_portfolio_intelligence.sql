-- Phase 20: private portfolio intelligence. Positions are user-entered research context, not trade
-- instructions. Valuation and risk analytics must consume source-linked market data only.

create table if not exists public.portfolios (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  name text not null,
  base_currency text not null default 'INR',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint portfolios_name_nonempty check (length(btrim(name)) between 1 and 80),
  constraint portfolios_currency_format check (base_currency ~ '^[A-Z]{3}$'),
  unique (user_id, name)
);

create table if not exists public.portfolio_positions (
  portfolio_id uuid not null references public.portfolios(id) on delete cascade,
  security_id uuid not null references public.securities(id) on delete cascade,
  quantity numeric(28,8) not null check (quantity > 0),
  average_cost numeric(28,8) check (average_cost is null or average_cost >= 0),
  notes text,
  added_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (portfolio_id, security_id),
  constraint portfolio_positions_notes_length check (notes is null or length(notes) <= 1000)
);

create index if not exists portfolios_user_updated_idx
  on public.portfolios(user_id, updated_at desc);
create index if not exists portfolio_positions_security_idx
  on public.portfolio_positions(security_id);

alter table public.portfolios enable row level security;
alter table public.portfolio_positions enable row level security;

revoke all on public.portfolios from anon, authenticated;
revoke all on public.portfolio_positions from anon, authenticated;
grant select, insert, update, delete on public.portfolios to authenticated;
grant select, insert, update, delete on public.portfolio_positions to authenticated;

drop policy if exists portfolios_owner_all on public.portfolios;
create policy portfolios_owner_all
  on public.portfolios
  for all
  to authenticated
  using (user_id = (select auth.uid()))
  with check (user_id = (select auth.uid()));

drop policy if exists portfolio_positions_owner_all on public.portfolio_positions;
create policy portfolio_positions_owner_all
  on public.portfolio_positions
  for all
  to authenticated
  using (
    exists (
      select 1 from public.portfolios p
      where p.id = portfolio_positions.portfolio_id
        and p.user_id = (select auth.uid())
    )
  )
  with check (
    exists (
      select 1 from public.portfolios p
      where p.id = portfolio_positions.portfolio_id
        and p.user_id = (select auth.uid())
    )
  );
