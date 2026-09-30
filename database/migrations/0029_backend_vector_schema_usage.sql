-- The dedicated backend role must resolve pgvector's type during embedding writes.
-- Grant schema USAGE only; do not grant CREATE or expand table privileges.
do $$
declare
    vector_schema text;
begin
    if exists (select 1 from pg_roles where rolname = 'india_finance_app') then
        select n.nspname into vector_schema
        from pg_extension e
        join pg_namespace n on n.oid = e.extnamespace
        where e.extname = 'vector';
        if vector_schema is not null then
            execute format(
                'grant usage on schema %I to india_finance_app',
                vector_schema
            );
        end if;
    end if;
end
$$;
