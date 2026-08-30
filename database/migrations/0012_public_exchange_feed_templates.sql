-- Development-only public exchange source templates. They are deliberately disabled.
-- Production should use a licensed exchange/corporate-data feed where required by terms.
insert into official_data_feeds (
  name, provider, feed_type, source_url, exchange, title,
  parser_config, poll_interval_seconds, enabled, next_run_at
) values
(
  'NSE public corporate announcements (development template)',
  'NSE',
  'exchange_disclosures',
  'https://www.nseindia.com/api/corporate-announcements?index=equities',
  'NSE',
  'NSE corporate announcements',
  '{"fetch_mode":"nse_public_session","usage":"development_only_public_web","production_requires_licensing_review":true}'::jsonb,
  900,
  false,
  now()
),
(
  'BSE public corporate announcements (development template)',
  'BSE',
  'exchange_disclosures',
  'https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w',
  'BSE',
  'BSE corporate announcements',
  '{"fetch_mode":"bse_public_api","lookback_days":1,"max_pages":4,"usage":"development_only_observed_public_route","production_requires_licensing_review":true}'::jsonb,
  900,
  false,
  now()
)
on conflict do nothing;
