# Official peer-input recovery

Branch: `codex/official-peer-inputs`. Base: `697ccfc`.

The existing metric bundle mixed independent official financial ratios with Yahoo
price ratios. Because source approval correctly required every upstream source to
be approved, this made official financial ratios inherit a price feed restriction.
Partitioning by each metric's exact upstream source set preserves both approvals
and restrictions without approving a restricted input.

The collector now recognizes stored NSE Assets and bank after-extraordinary-item
EPS/PAT concept names. It can calculate annual EBIT-based ROCE and EBITDA margin
from reported PBT, finance costs and depreciation. Components must have the same
annual period, source, start date, XBRL context and units. PBT-derived EBITDA is
explicitly labeled as including non-operating items; it is not adjusted operating
EBITDA. ROCE uses an annual numerator and same-date balance-sheet components.

The official EOD input collector downloads one bounded final NSE UDiFF archive.
It requires an exact member name, bounded sizes, all requested symbols and ISINs,
EQ series, trading date and valid OHLCV. It stores the downloaded archive checksum
and actual official URI through the existing reference provenance policy. No
source permission is changed for existing Yahoo inputs.

## Operator commands

Within the existing runtime, with its existing database/provider configuration:

```sh
python scripts/collect_official_peer_inputs.py --stored-metrics-only --dry-run
python scripts/collect_official_peer_inputs.py --stored-metrics-only
python scripts/collect_official_peer_inputs.py --session 2026-09-30 --dry-run
python scripts/collect_official_peer_inputs.py --session 2026-09-30
```

Run validation before its matching write. Official price-file failure must not
prevent independent stored financial calculations. Future runs must explicitly
choose an actual completed session within seven days. This is a bounded recovery,
not a recurring schedule. Stop at the existing 450 MB storage guard. Remove any
one-time deployment hooks after execution.

The prepared universe is selected from existing readiness rows, capped at 50.
Readiness is recalculated from actual data using runtime configuration. The
collector can store valid partial metric sets but cannot mark them complete;
the existing minimum-three-metric check remains unchanged.

## Verification and limitations

Local focused tests: 27 passed. Ruff passed. Full application typecheck passed
(149 source files). CI must pass before merge/deployment. No migrations or credentials are added.

A read-only snapshot preview found 29 stocks with three comparable financial
ratios without new prices. This is not a production-ready claim. Official
classification access remains blocked, and no category or taxonomy is inferred.
The initial launch scope remains 50 securities; this is not full NSE coverage.

Rollback: deploy the previous reviewed application commit. New real source-linked
metrics/EOD rows remain auditable and need not be deleted. Restricted old sources
remain restricted. Verify actual post-import peer/valuation coverage independently.
