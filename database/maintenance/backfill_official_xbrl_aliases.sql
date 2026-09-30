-- Operator backfill: exact aliases of existing official NSE XBRL values.
-- Validate candidate counts and existing canonical conflicts before execution.
-- Retain original raw rows, values, periods, source IDs and XBRL metadata.
-- Idempotent; do not overwrite existing canonical observations.
with aliases(raw_name, canonical_name) as (
    values
      ('profit_loss_for_period', 'pat'),
      ('basic_earnings_loss_per_share_from_continuing_and_discontinued_operations', 'eps_basic'),
      ('diluted_earnings_loss_per_share_from_continuing_and_discontinued_operations', 'eps_diluted'),
      ('depreciation_depletion_and_amortisation_expense', 'depreciation_amortization')
), candidates as (
    select f.*, a.canonical_name
    from financial_facts f
    join aliases a on a.raw_name = f.fact_name
    join sources s on s.id = f.source_id
    where s.source_type = 'exchange_filing'
      and s.source_uri ~ '^https://(nsearchives|archives)\.nseindia\.com/'
      and s.checksum ~ '^[0-9a-f]{64}$'
      and f.data->>'source_format' = 'xbrl'
), inserted as (
    insert into financial_facts (
      security_id, fact_name, period_start, period_end, period_type,
      value, unit, source_id, data
    )
    select security_id, canonical_name, period_start, period_end, period_type,
           value, unit, source_id,
           data || jsonb_build_object(
             'canonical_alias_of', fact_name,
             'normalization_rule', 'official-xbrl-total-result-aliases-v1'
           )
    from candidates
    where pg_database_size(current_database()) < 450000000
    on conflict (security_id, fact_name, period_end, period_type, source_id) do nothing
    returning fact_name
)
select fact_name, count(*) as inserted_rows
from inserted group by fact_name order by fact_name;
