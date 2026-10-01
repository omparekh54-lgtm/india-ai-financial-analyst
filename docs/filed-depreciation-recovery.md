# Filed depreciation metric recovery

The prepared universe has 11 annual filings whose P&L depreciation expense is stored as
`depreciation_depletion_and_amortisation_expense`, but the derived margin calculator previously
recognized only `depreciation_amortization`. Recognize the raw name only when its XBRL element
is exactly `DepreciationDepletionAndAmortisationExpense`. Preserve the original fact and source.
Existing annual period, start date, source, context, unit and nonnegative component checks apply.
Cash-flow adjustments and depreciation-only concepts are not substitutes.

Validation: 38 focused ingestion/metric tests passed; targeted Ruff and whitespace checks passed.
No migration, credential or new dependency is required. GitHub CI must pass before deployment.

Operator: deploy the reviewed commit to the existing research-worker with the bounded pre-deploy
command `python scripts/collect_official_peer_inputs.py --stored-metrics-only`. This recalculates
prepared-stock metrics from stored source-linked facts and refreshes readiness without downloading
prices. Wait for the command to complete, audit production counts, then restore preDeployCommand
to an empty list and deploy normal worker operation. Do not clear the command during building.

Baseline: 185 approved recent metrics, 29/50 peer metric coverage, 28/50 valuation readiness.
Recompute actual coverage after execution; do not assume all 11 stocks cross readiness thresholds.
Official four-tier classifications and approved price-dependent ratios remain separate gaps.
Rollback: restore the preceding worker commit. Keep valid deterministic rows; no destructive cleanup.
