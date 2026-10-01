# LT issuer history recovery

The approved-source audit shows LT has six distinct financial reporting dates and needs eight.
The official issuer investor website hosts the consolidated January 30, 2025 results PDF at
https://investors.larsentoubro.com/upload/Quarterly/FY2025QuarterlyFY2025%20Quarterly%20Financial%20Result%20Dec%202024.pdf.
Page 1 reports the December and September 2024 quarters as its first two columns. The issuer
name and CIN L99999MH1946PLC004768 agree with LT's official issuer website and published filing;
the importer separately requires canonical NSE ISIN INE018A01030 before writes.

Source-governance review: approve this exact issuer document and two extracted reporting columns
through the pull request containing this runbook. Pass that reviewed PR URL as --approval-reference
to the existing reference provenance policy. This does not change global domain recognition or
any existing source permissions. Metadata retains licensed_or_approved and issuer authority.
This reference is the review record for the verified issuer filing, not a claim of a vendor license.

The bounded importer pins the downloaded PDF SHA-256
7285dfb8b33c97c0380e1e34b58ea338df12544c1ec178e4e33b3ae2f876e8b7, seven pages, exact issuer,
consolidated heading, six-column date order, crore/share units and ten unambiguous statement rows.
Income/expense totals must reconcile. It imports 20 genuine facts and two source-linked evidence
chunks through the existing schema and refreshes LT readiness. No artificial facts, changed
history threshold, new provider subscription or migration is involved.

Validate with --dry-run before the matching write on the existing research-worker. Keep its normal
start command and wait for import completion before clearing temporary pre-deploy commands.
Audit source checksum, two reporting periods, fact/evidence counts and LT history/readiness.
Restore normal worker deployment afterwards. If retrieval or checksum validation fails, stop safely.
Rollback the application commit if necessary; retain the real, auditable source-linked rows.
