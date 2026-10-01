# Reported bank and insurer peer calculations

The existing peer calculator now recognizes these exact filed XBRL concepts
without rewriting the original financial facts or relaxing the three-metric gate:

- Bank net interest income growth compares `InterestEarned - InterestExpended`
  with the comparable prior-year period. Each pair must share source, period,
  start date and XBRL context, use INR, and have positive net interest income.
  The growth metric explicitly identifies net interest income as its basis.
- Bank price/book uses reported `Capital + ReservesAndSurplus` and ordinary
  shares previously derived from paid-up equity capital. All three inputs must
  share a filing and balance-sheet date; capital and reserves share an instant
  context. It identifies this exact book-equity basis rather than silently
  substituting total assets, deposits or policyholder funds.
- Insurer EPS recognizes the exact filed combined basic/diluted EPS after
  extraordinary items. P/E still requires annual or TTM EPS, never an annualized
  quarter. Gross premium growth retains its explicit reported premium basis.

Actual stored facts for AXISBANK, HDFCBANK, ICICIBANK, KOTAKBANK and SBIN
produce guarded bank growth and price/book calculations. Insurer EPS and premium
growth can be calculated for HDFCLIFE and SBILIFE; a third comparable metric
still requires valid book value and ordinary share inputs. Insurer policyholder
funds must never be substituted for shareholder equity.

Every calculation records exact upstream source IDs. Existing source approval,
period freshness and metric partitioning apply unchanged. Production recalculation
uses the existing prepared-universe backfill and is checked independently through
database coverage; unit tests and a deployment alone do not prove readiness.
