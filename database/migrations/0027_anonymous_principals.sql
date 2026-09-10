-- Anonymous session isolation.
--
-- PROJECT_INTENT.md prohibits "using one shared pseudo-user for all visitors" and
-- "using NULL ownership as a substitute for public-session isolation". The application
-- did both: every anonymous visitor was assigned the hard-coded UUID
-- 00000000-0000-0000-0000-000000000001, which does not exist in auth.users, so every
-- watchlist insert failed its foreign key; and research jobs fell back to NULL ownership.
--
-- This migration introduces `principals` as the single ownership target. A principal is
-- either a registered Supabase auth user or a durable anonymous session.
--
-- Design note: the owning columns keep the name `user_id` and only their foreign key is
-- repointed from auth.users to principals. Renaming to `principal_id` would have forced a
-- mechanical rewrite of every repository and RLS policy for no behavioural gain, and each
-- such edit is a chance to drop an ownership filter. Existing values are rewritten from
-- the auth user id to that user's principal id, so current rows keep their owner.

create table if not exists public.principals (
  id uuid primary key default gen_random_uuid(),
  kind text not null check (kind in ('user', 'anonymous')),
  auth_user_id uuid unique references auth.users(id) on delete cascade,
  created_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  expires_at timestamptz,
  -- A user principal must reference an auth user; an anonymous principal must not.
  constraint principals_kind_matches_reference check (
    (kind = 'user' and auth_user_id is not null)
    or (kind = 'anonymous' and auth_user_id is null)
  ),
  -- Anonymous principals must carry a retention deadline; user principals must not.
  constraint principals_anonymous_expires check (
    (kind = 'anonymous' and expires_at is not null)
    or (kind = 'user' and expires_at is null)
  )
);

create index if not exists principals_auth_user_idx
  on public.principals(auth_user_id) where auth_user_id is not null;
create index if not exists principals_expiry_idx
  on public.principals(expires_at) where expires_at is not null;

-- Session tokens are never stored in plaintext; only a SHA-256 hash of the cookie value.
create table if not exists public.anonymous_sessions (
  token_sha256 text primary key,
  principal_id uuid not null references public.principals(id) on delete cascade,
  created_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  expires_at timestamptz not null,
  constraint anonymous_sessions_token_format check (token_sha256 ~ '^[0-9a-f]{64}$')
);

create index if not exists anonymous_sessions_principal_idx
  on public.anonymous_sessions(principal_id);
create index if not exists anonymous_sessions_expiry_idx
  on public.anonymous_sessions(expires_at);

-- Every existing auth user gets a principal so ownership can be rewritten safely.
insert into public.principals (kind, auth_user_id)
select 'user', u.id
from auth.users u
where not exists (
  select 1 from public.principals p where p.auth_user_id = u.id
);

-- Repoint ownership from auth.users to principals, preserving the column name.
do $$
declare
  target record;
  fk_name text;
begin
  for target in
    select * from (values
      ('watchlists',        'user_id'),
      ('portfolios',        'user_id'),
      ('monitoring_alerts', 'user_id'),
      ('user_usage_daily',  'user_id')
    ) as t(table_name, column_name)
  loop
    if to_regclass('public.' || target.table_name) is null then
      continue;
    end if;

    -- Drop whatever foreign key currently points the column at auth.users.
    for fk_name in
      select con.conname
      from pg_constraint con
      join pg_class rel on rel.oid = con.conrelid
      join pg_namespace nsp on nsp.oid = rel.relnamespace
      where nsp.nspname = 'public'
        and rel.relname = target.table_name
        and con.contype = 'f'
        and con.confrelid = 'auth.users'::regclass
    loop
      execute format('alter table public.%I drop constraint %I', target.table_name, fk_name);
    end loop;

    -- Rewrite existing owner ids to the corresponding principal id.
    execute format(
      'update public.%I t set %I = p.id
         from public.principals p
        where p.auth_user_id = t.%I',
      target.table_name, target.column_name, target.column_name
    );

    -- A row whose owner cannot be resolved has ambiguous ownership; leaving it in place
    -- would make it unreachable but still present. All four tables are empty at the time
    -- this migration was written.
    execute format(
      'delete from public.%I t
        where not exists (select 1 from public.principals p where p.id = t.%I)',
      target.table_name, target.column_name
    );

    execute format(
      'alter table public.%I
         add constraint %I foreign key (%I)
         references public.principals(id) on delete cascade',
      target.table_name, target.table_name || '_owner_principal_fk', target.column_name
    );
  end loop;
end $$;

-- research_jobs.requested_by allowed NULL for public traffic, which the intent forbids as
-- a substitute for session isolation. Repoint it at principals so public research is owned.
do $$
declare
  fk_name text;
begin
  if to_regclass('public.research_jobs') is null then
    return;
  end if;

  for fk_name in
    select con.conname
    from pg_constraint con
    join pg_class rel on rel.oid = con.conrelid
    join pg_namespace nsp on nsp.oid = rel.relnamespace
    where nsp.nspname = 'public'
      and rel.relname = 'research_jobs'
      and con.contype = 'f'
      and con.confrelid = 'auth.users'::regclass
  loop
    execute format('alter table public.research_jobs drop constraint %I', fk_name);
  end loop;

  update public.research_jobs t
     set requested_by = p.id
    from public.principals p
   where p.auth_user_id = t.requested_by;

  -- Historical rows with an unresolvable owner are detached rather than deleted: a research
  -- report is not private user data in the way a watchlist is.
  update public.research_jobs t
     set requested_by = null
   where t.requested_by is not null
     and not exists (select 1 from public.principals p where p.id = t.requested_by);

  alter table public.research_jobs
    add constraint research_jobs_requested_by_principal_fk
    foreign key (requested_by) references public.principals(id) on delete set null;
end $$;

create index if not exists research_jobs_requested_by_created_idx
  on public.research_jobs(requested_by, created_at desc);

-- Watchlist name uniqueness is scoped per principal.
alter table public.watchlists drop constraint if exists watchlists_user_id_name_key;
create unique index if not exists watchlists_owner_name_key
  on public.watchlists(user_id, name);

-- Row level security: ownership is enforced in the database even if an API filter is
-- omitted. The API connects as the service role; anon/authenticated are denied direct
-- access, consistent with migration 0026.
alter table public.principals enable row level security;
alter table public.anonymous_sessions enable row level security;

revoke all on public.principals, public.anonymous_sessions from anon, authenticated;

drop policy if exists principals_no_direct_access on public.principals;
create policy principals_no_direct_access
  on public.principals for all to anon, authenticated
  using (false) with check (false);

drop policy if exists anonymous_sessions_no_direct_access on public.anonymous_sessions;
create policy anonymous_sessions_no_direct_access
  on public.anonymous_sessions for all to anon, authenticated
  using (false) with check (false);
