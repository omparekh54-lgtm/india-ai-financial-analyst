# Production data provenance policy

The production research corpus is **real-data-only**.

## Non-negotiable rule

Do not insert generated, synthetic, mock, fake, dummy, fixture, sample, placeholder or otherwise fabricated market/research data into the production Supabase database to satisfy coverage, demos, tests or readiness checks.

This applies to:

- securities and provider-instrument mappings;
- financial facts and company metrics;
- corporate events, filings and evidence chunks;
- security OHLCV/history;
- benchmark/index/volatility history;
- macro, flow and policy observations;
- comparable/peer metrics;
- live-market snapshots used by research.

If real source data is unavailable, the correct state is **missing/incomplete data**, not fabricated data.

## Accepted production provenance

Production rows must originate from a traceable external source appropriate to the dataset, such as:

- NSE/BSE or an approved/licensed exchange data product;
- SEBI, RBI, NSDL or another authoritative regulator/institution;
- an issuer's official filing, investor-relations document or earnings material;
- an authenticated broker/market-data provider used under its applicable terms;
- an approved/licensed third-party data vendor;
- an approved export supplied with its original source URI and checksum.

Recognized official/regulatory source domains are classified as `official_source` and do not require a separate manual approval reference. A non-official source is accepted only when it carries an explicit approval reference identifying the applicable license, contract, procurement/source-governance approval, or equivalent audit record; it is classified as `licensed_or_approved`. A real-looking URL by itself is not sufficient approval for a non-official source.

Where the schema supports `source_id`, material production rows must be linked to a persisted `sources` record. Evidence chunks are always source-linked by schema.

## Enforcement already in code and database

- The production bootstrap requires explicit provenance for financial, market and comparable-metric imports.
- Generic `reference_*` importers require `--approval-reference` for non-official sources and persist the approval classification/reference into `sources.metadata`.
- Official benchmark bootstrap accepts only approved NSE/NSE Indices HTTPS domains.
- Official macro bootstrap accepts only approved RBI/NSDL HTTPS domains and supported canonical series.
- Reference source URIs, providers and approval references explicitly marked synthetic/mock/fake/dummy/fixture/sample/generated/placeholder are rejected.
- PostgreSQL constraint `sources_reference_production_approved_chk` rejects a `reference_*` source row unless its metadata contains `production_approved=true`, preventing direct SQL or future scripts from bypassing importer-level approval checks.
- Production preflight requires the validated approval constraint to exist before the runtime is considered ready.
- Corpus readiness fails if populated financial facts, corporate events, security market bars, benchmark bars, macro observations or comparable metrics contain rows without source provenance.
- Corpus readiness also fails if non-production provenance is detected or an official feed that still requires production licensing/source review is enabled without the required approval state.
- The full NSE EQ universe remains a hard readiness prerequisite; the system must not fill the gap using fabricated securities.

## Derived calculations are not synthetic source data

Deterministic derived values are permitted only when they are calculated from real source-linked components and remain labeled as derived. Examples include EBITDA derived from sourced EBIT plus depreciation/amortization and free cash flow derived from sourced operating cash flow and capex. Derived calculations must never be used to invent missing source facts.

## Test fixtures

Synthetic values may exist **only inside isolated automated tests** where they validate calculations, parsers, agents or failure behavior. Test fixtures must not be loaded into the connected production Supabase project and must not be presented as real market/company data. Production smoke verification is read-only.

## Operational rule

Every production import should be dry-run/validated first, record or preserve source provenance, and report a checksum/count. If provenance or production usage approval cannot be established, stop the import and leave the corresponding readiness warning visible. Do not replace an inaccessible official source with an unofficial mirror or generated rows merely to improve coverage metrics.
