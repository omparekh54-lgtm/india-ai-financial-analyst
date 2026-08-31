# Production source governance

Record ID: `SG-2026-08-31-01`

## Decision

The production research corpus must contain only real, externally sourced observations. Synthetic, mock, fake, dummy, fixture, sample, generated, placeholder, or otherwise fabricated market/fundamental/research rows are prohibited from production ingestion and must never be inserted merely to satisfy a readiness threshold.

When an exchange/regulator delivery route is inaccessible from the deployed runtime, a documented real-data provider may be used as a fallback if its provenance is preserved and its authority is not overstated.

## Source precedence

1. Exchange, regulator, depository, central bank/statistical authority, or issuer primary document/feed.
2. Regulated broker/reference source carrying exchange identifiers or exchange-sourced market observations.
3. Approved market-data aggregator/reference provider for historical/delayed observations when a primary route is unavailable.
4. Web/news sources for contextual intelligence only; they do not independently verify primary financial facts.

A lower-precedence source never overrides a conflicting higher-precedence source without an explicit reconciliation record.

## Approved fallback integrations

### Upstox BOD instrument files

- Purpose: bootstrap/refresh NSE/BSE instrument metadata, ISINs, trading symbols, provider instrument keys, and broker mapping when the exchange security-master artifact cannot be fetched by the runtime.
- Source documentation: `https://upstox.com/developer/api-documentation/instruments/`
- NSE BOD artifact: `https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz`
- Classification: `licensed_or_approved` / regulated-broker reference.
- Restrictions: import cash-equity records only for the canonical equity master; reject test/dummy/non-production identifiers; retain Upstox as the provider mapping and do not label the artifact as an NSE-primary file.

### Yahoo Finance fallback

- Purpose: delayed/historical OHLCV and secondary reference metrics only when approved primary/broker history is unavailable.
- Classification: `licensed_or_approved` secondary market-data reference.
- Restrictions: never label as live exchange data, never use to independently verify exchange-filed financial facts, store the exact provider URI/retrieval time, and keep it below exchange/issuer/broker-primary observations in evidence precedence.

This record is an internal source-governance approval for technical use; it is not a representation that OpenAI or the application operator has obtained a separate commercial data redistribution license. Commercial/public launch must still confirm the applicable provider terms for the intended usage and redistribution model.

## Enforcement

- Every material financial fact, corporate event, market bar, benchmark bar, macro observation, and peer/security metric must retain a source/provenance link.
- Importers must fail closed on malformed identifiers, suspiciously small files, duplicate natural keys, non-finite values, and non-production source markers.
- Production readiness must fail if synthetic/non-production source markers are detected.
- Optional external/network integrations remain behind runtime switches and may not be enabled merely to make readiness checks green.
