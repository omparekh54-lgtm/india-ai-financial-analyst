# Official classification recovery

Verified on 1 October 2026: peer metrics and valuation pass for 50 prepared
securities; official four-tier classification and full research remain 0/50.
The existing quote API timed out. A read-only Railway probe of the newer
`NextApi/apiClient/GetQuoteApi?functionName=getSymbolData&marketType=N&series=EQ&symbol=HDFCBANK`
also ended in `httpx.ReadTimeout` (deployment
`70b54d4d-d547-4276-9780-09993018e552`). No classification was imported.
The temporary hook was removed and normal commit `74822b2` successfully deployed
as `1912df29-408e-4709-b6e2-6d48ba6fd53a`.

## Original export import

`scripts/import_prepared_classification_snapshot.py` supports an operator-reviewed
export of original legacy NSE quote responses. It does not fetch or invent data.
Do not supply hand-authored labels, secondary-provider classifications, index
constituent broad sectors, ESG labels or test fixtures. The new NextApi payload
is not supported until its genuine response contract has been verified.

The export directory must contain a `manifest.json` with `schema_version: 1`
and exactly 50 `responses`. Each response entry identifies:

| Field | Required value |
| --- | --- |
| `symbol` | Exact prepared NSE symbol |
| `file` | Local filename containing original JSON response bytes |
| `source_uri` | Exact official `https://www.nseindia.com/api/quote-equity?symbol=...`, URL-encoded symbol |
| `sha256` | SHA-256 of the original response bytes |
| `retrieved_at` | Actual timezone-aware collection timestamp within 30 days |

The original response must contain the matching explicit symbol and ISIN and
all four non-empty official industry labels. The operator must review its origin;
a checksum alone does not prove authenticity. Keep original files in the existing
private ingestion staging location; do not commit production exports to Git.

Validate first in the configured API runtime:

```sh
python scripts/import_prepared_classification_snapshot.py --manifest /private/export/manifest.json --approval-reference REVIEW_REFERENCE
```

After validating the exact export and its origin, run the same command with
`--apply`. All 50 files are validated before any database write. The existing
450 MB storage guard applies. Imports preserve original response checksums and
collection times, use the existing classification source contract, and recalculate
prepared readiness. Success requires the resulting orchestrator count to be 50.
Other genuine blockers remain visible; importing metadata does not prove that a
complete report has executed. No migration or new provider credential is needed.

No original export was available during implementation. This importer is a
prepared recovery path, not evidence of new production coverage. After any
one-time run, clear temporary deployment hooks and verify the normal worker.
