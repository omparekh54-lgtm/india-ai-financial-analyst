-- Canonical, idempotent equivalent of the early connected-Supabase
-- `security_hardening` migration that was applied outside the numbered
-- repository migration sequence.
--
-- This preserves fresh-environment reproducibility without mutating or
-- rewriting historical migration records in the connected project.

create schema if not exists extensions;

do $$
begin
  if exists (
    select 1
    from pg_extension e
    join pg_namespace n on n.oid = e.extnamespace
    where e.extname = 'vector'
      and n.nspname <> 'extensions'
  ) then
    execute 'alter extension vector set schema extensions';
  end if;
end
$$;

do $$
begin
  if to_regprocedure('public.rls_auto_enable()') is not null then
    execute 'revoke execute on function public.rls_auto_enable() from anon, authenticated, public';
  end if;
end
$$;
