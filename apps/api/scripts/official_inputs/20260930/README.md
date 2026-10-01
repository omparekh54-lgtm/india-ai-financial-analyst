# Reviewed official NSE inputs

Retrieved directly from NSE on 2026-10-01 for the completed 2026-09-30 session:

| File | Official source | Bytes | SHA-256 |
| --- | --- | --- | --- |
| full-delivery.csv | https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_30092026.csv | 399899 | d7c50dbb8c51d6dec20cee52bb478887a5d3c95946fa27539e89b83476ce0d89 |
| nifty50-identity.csv | https://nsearchives.nseindia.com/content/indices/ind_nifty50list.csv | 3344 | f5ba4027d935ae72879d5c24bbebf0e8439ec8ed504ffdf4175fde69206244b3 |

These are original public source files, not database exports. Both files passed
the existing strict parser against all 50 prepared symbols and ISINs. The delivery
file has no ISIN, so the independent constituent file is mandatory. Its broad
Industry field does not satisfy the official four-tier classification contract.

The bounded `--reviewed-inputs` option avoids repeating unreliable downloads in
the deployment network. It verifies exact file sizes and hashes before applying
the normal session, identity, complete coverage, finite OHLC and volume checks.
The original official URIs and checksums remain the source provenance. This
bundle is usable only within the existing seven-day freshness window; it is not
a replacement for recurring EOD downloads. No source approval is overridden.

Dry-run and apply must be separate Railway pre-deploy commands/deployments:

```
python scripts/collect_official_peer_inputs.py --session 2026-09-30 --report full-delivery --reviewed-inputs --dry-run
python scripts/collect_official_peer_inputs.py --session 2026-09-30 --report full-delivery --reviewed-inputs
```

Clear the temporary pre-deploy command after verified completion. No schema,
credentials, paid dependencies or readiness thresholds change.
