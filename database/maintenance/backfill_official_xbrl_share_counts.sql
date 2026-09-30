-- Derive paid-up equity shares, not weighted-average EPS shares; retain original facts.
-- Require the same official filing, context, currency and period; never round or overwrite.
with candidates as (
  select p.security_id, p.period_start, p.period_end, p.period_type, p.source_id,
         p.value / f.value as shares, p.data->>'xbrl_context_id' as context_id
  from financial_facts p
  join financial_facts f
    on f.security_id = p.security_id and f.source_id = p.source_id
   and f.period_end = p.period_end and f.period_type = p.period_type
   and f.period_start is not distinct from p.period_start
   and f.data->>'xbrl_context_id' = p.data->>'xbrl_context_id'
  join sources s on s.id = p.source_id
  where p.fact_name in ('paid_up_value_of_equity_share_capital', 'paid_up_equity_share_capital')
    and f.fact_name in ('face_value_of_equity_share_capital', 'equity_share_face_value')
    and p.unit = 'INR' and f.data->>'xbrl_unit_ref' = 'INRPerShare'
    and p.value > 0 and f.value > 0
    and p.value / f.value = trunc(p.value / f.value)
    and p.data->>'source_format' = 'xbrl' and f.data->>'source_format' = 'xbrl'
    and s.source_type = 'exchange_filing'
    and s.source_uri ~ '^https://(nsearchives|archives)\.nseindia\.com/'
    and s.checksum ~ '^[0-9a-f]{64}$'
), inserted as (
  insert into financial_facts (security_id, fact_name, period_start, period_end,
                              period_type, value, unit, source_id, data)
  select distinct security_id, 'shares_outstanding', period_start, period_end,
         period_type, shares, 'shares', source_id,
         jsonb_build_object(
           'derived', true, 'calculation_version', 1,
           'formula', 'paid_up_equity_share_capital / equity_share_face_value',
           'components', jsonb_build_array('paid_up_equity_share_capital', 'equity_share_face_value'),
           'share_count_basis', 'paid_up_equity_capital', 'xbrl_context_id', context_id
         )
  from candidates
  where pg_database_size(current_database()) < 450000000
  on conflict (security_id, fact_name, period_end, period_type, source_id) do nothing
  returning security_id
)
select count(*) as inserted_share_counts, count(distinct security_id) as securities
from inserted;
