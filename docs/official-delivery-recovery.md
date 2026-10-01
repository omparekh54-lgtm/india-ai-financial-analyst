# Official delivery-report EOD recovery

The UDiFF archive timed out on Railway and GitHub. The alternate input is NSE's
published Full Bhavcopy and Security Deliverable data report, rather than a mirror
of the inaccessible archive. It contains prices and volumes but no ISIN.

The collector therefore requires a fresh official NIFTY 50 constituent CSV and
matches its unique EQ symbol/ISIN pair to every prepared target. Both file URIs,
the report checksum, identity-file checksum and retrieval timestamp are retained
in each price source. Missing or ambiguous identities, missing stocks, wrong
dates, non-finite/zero prices and invalid OHLCV fail before any price writes.

The one-time GitHub workflow uses the existing production database secret and
existing free-tier concurrency group. It first validates the report, then imports
at most 50 prepared stocks and recalculates their source-partitioned metrics.
Readiness is refreshed later in the Railway runtime so configured providers are
evaluated accurately. There is no new schedule or classification inference.

```sh
python scripts/collect_official_peer_inputs.py --session 2026-09-30 --report full-delivery --dry-run
python scripts/collect_official_peer_inputs.py --session 2026-09-30 --report full-delivery --skip-readiness-refresh
# In the configured Railway runtime after successful collection:
python scripts/collect_official_peer_inputs.py --refresh-readiness-only
```

Local verification: 37 parser/downloader/derived-metric tests passed; changed-file
Ruff checks passed. CI and actual production row counts remain required. The
official four-tier classification gap remains independent of this import.

Rollback: restore the preceding reviewed worker commit. Keep genuine imported
rows and source metadata auditable. Clear one-time deployment hooks after use.
