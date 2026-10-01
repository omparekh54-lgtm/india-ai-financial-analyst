# Reviewed insurer ordinary-share evidence

Source material was retrieved and checked on 2026-10-01. This directory contains
original public source material or a short exact source excerpt, not database exports.

## HDFC Life

Original issuer annual report (632 pages, 8,962,772 bytes):
https://www.hdfclife.com/content/dam/hdfclifeinsurancecompany/about-us/pdf/investor-relations/financial-information/annual-reports/Integrated-Annual-Report-FY-2025-26.pdf

Original PDF SHA-256: `b7a552082ee124037a5075412e3680b21ca6ede0c1d4c2c1c6a032afc0490e9c`.
The original PDF contains the issuer CIN `L65110MH2000PLC128245`; the canonical
HDFCLIFE security ISIN is separately verified as `INE795G01014` before import.

`hdfclife-page166.txt` is the short share-capital paragraph extracted from PDF
page 166 with PyMuPDF sorted text. Exact excerpt SHA-256:
`6c02f3df8975d46dda449b9ff621c6f66dc6c5ed42dc34a65f8b6b905ebedcd0`.
The importer separately records the original document and excerpt hashes. It does
not claim that the excerpt hash verifies the entire PDF. The paragraph reports
2,157,819,536 ordinary equity shares at 31 March 2026; capital reconciles exactly
to those shares times the reported INR 10 face value.

Source-governance review: PR52 approves these factual share-capital inputs from
the verified issuer report for this project. The importer requires that approval
reference and retains `licensed_or_approved` provenance; it does not globally
approve this domain or relabel any legacy provider source.

## SBI Life

`sbilife-original.xml` is the complete original NSE-hosted filing:
https://nsearchives.nseindia.com/corporate/xbrl/INTEGRATED_FILING_LI_1657299_22042026060213_WEB.xml

SHA-256: `1ec804878eccf10bd21d54602bc79bf5a3d58d566d421659634549947c406221`.
This matches the checksum of the already approved stored financial filing.
The parser verifies SBILIFE, `INE123W01016`, the 31 March 2026 instant and notes
contexts, reported INR equity capital, and an unambiguous INR 10 ordinary-equity
face value in those filed notes. It calculates 1,003,092,100 shares from reported
paid-up capital / face value. The capital's reported rounding (`decimals=-3`)
is preserved; this is a derived count from rounded capital, not an exact reported
registry count.

The bounded recovery imports two source-linked share facts and recalculates only
the remaining eleven prepared stocks using approved financial inputs and an
approved EOD market source. Price/book uses shareholder funds; insurer
policyholder funds are excluded. Neither source approval rules nor readiness
thresholds change. Dry-run, apply, SQL verification and clearing the temporary
Railway pre-deploy command are separate operator steps.
