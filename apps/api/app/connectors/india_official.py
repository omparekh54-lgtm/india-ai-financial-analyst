from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup

from app.ingestion.macro import MacroObservation

NSE_ANNOUNCEMENTS_PAGE = (
    "https://www.nseindia.com/companies-listing/corporate-filings-announcements"
)
NSE_FINANCIAL_RESULTS_PAGE = (
    "https://www.nseindia.com/companies-listing/corporate-filings-financial-results"
)
BSE_CORPORATES_PAGE = "https://m.bseindia.com/corporates.aspx"
RBI_DATA_RELEASES_PAGE = "https://statistics.rbi.org.in/"
NSDL_FPI_REPORTS_PAGE = "https://pilot.fpi.nsdl.co.in/Reports/ReportsListing.aspx"


@dataclass(frozen=True)
class OfficialDisclosureRecord:
    exchange: str
    company_name: str
    headline: str
    published_at: datetime | None
    source_uri: str
    nse_symbol: str | None = None
    bse_code: str | None = None
    details: str | None = None
    attachment_url: str | None = None
    xbrl_url: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class MacroSeriesSpec:
    series_key: str
    unit: str | None = None
    date_column: str | None = None
    value_column: str | None = None


def parse_exchange_disclosures(
    exchange: str,
    data: bytes,
    media_type: str,
    *,
    source_uri: str | None = None,
) -> list[OfficialDisclosureRecord]:
    normalized = exchange.strip().upper()
    if normalized == "NSE":
        return parse_nse_disclosures(data, media_type, source_uri=source_uri)
    if normalized == "BSE":
        return parse_bse_disclosures(data, media_type, source_uri=source_uri)
    raise ValueError("exchange must be NSE or BSE")


def parse_nse_disclosures(
    data: bytes,
    media_type: str,
    *,
    source_uri: str | None = None,
) -> list[OfficialDisclosureRecord]:
    default_source = source_uri or NSE_ANNOUNCEMENTS_PAGE
    records = _records_from_payload(data, media_type)
    output: list[OfficialDisclosureRecord] = []
    for row in records:
        symbol = _first(row, "symbol", "nse symbol", "nse_symbol")
        company = _first(row, "company name", "company", "name")
        subject = _first(row, "subject", "event subject", "events subject", "event / subject")
        details = _first(row, "details", "detail", "description", "remarks")
        published = _first(
            row,
            "broadcast date/time",
            "broadcast date time",
            "broadcast date",
            "date time",
            "date",
        )
        attachment = _absolute_url(
            _first(row, "attachment", "attachment url", "file link", "document"),
            default_source,
        )
        xbrl = _absolute_url(_first(row, "xbrl", "xbrl file link", "xbrl link"), default_source)
        headline = subject or details
        if not symbol or not company or not headline:
            continue
        output.append(
            OfficialDisclosureRecord(
                exchange="NSE",
                nse_symbol=symbol.upper(),
                company_name=company,
                headline=headline,
                details=details,
                published_at=_parse_datetime(published),
                source_uri=attachment or xbrl or default_source,
                attachment_url=attachment,
                xbrl_url=xbrl,
                metadata={
                    "raw_subject": subject,
                    "official_page": default_source,
                },
            )
        )
    return _dedupe_disclosures(output)


def parse_bse_disclosures(
    data: bytes,
    media_type: str,
    *,
    source_uri: str | None = None,
) -> list[OfficialDisclosureRecord]:
    default_source = source_uri or BSE_CORPORATES_PAGE
    normalized_media = media_type.split(";", 1)[0].strip().lower()
    if normalized_media in {"text/html", "application/xhtml+xml"}:
        html_records = _parse_bse_mobile_html(data, default_source)
        if html_records:
            return _dedupe_disclosures(html_records)

    records = _records_from_payload(data, media_type)
    output: list[OfficialDisclosureRecord] = []
    for row in records:
        code = _first(row, "scrip_cd", "scrip cd", "security code", "scrip code", "code")
        company = _first(
            row,
            "slongname",
            "company name",
            "security name",
            "scrip name",
            "short name",
        )
        subject = _first(
            row,
            "newssub",
            "news subject",
            "news_subject",
            "subject",
            "headline",
        )
        details = _first(row, "headline", "details", "description", "news body", "remarks")
        published = _first(
            row,
            "news_dt",
            "news dt",
            "news_submission_dt",
            "news submission dt",
            "dt_tm",
            "date time",
            "date",
        )
        attachment = _absolute_url(
            _first(
                row,
                "attachmentname",
                "attachment name",
                "attachmenturl",
                "attachment url",
                "nsurl",
                "file link",
            ),
            default_source,
        )
        headline = subject or details
        if not code or not company or not headline:
            continue
        output.append(
            OfficialDisclosureRecord(
                exchange="BSE",
                bse_code=_digits(code),
                company_name=company,
                headline=headline,
                details=details,
                published_at=_parse_datetime(published),
                source_uri=attachment or default_source,
                attachment_url=attachment,
                metadata={
                    "raw_subject": subject,
                    "official_page": default_source,
                },
            )
        )
    return _dedupe_disclosures(output)


def parse_rbi_macro_series(
    data: bytes,
    media_type: str,
    spec: MacroSeriesSpec,
) -> list[MacroObservation]:
    rows = _records_from_payload(data, media_type)
    if not rows:
        return []

    date_key = _resolve_column(
        rows[0],
        requested=spec.date_column,
        candidates=("date", "observation date", "period", "reference date", "as on"),
    )
    value_key = _resolve_numeric_column(
        rows,
        requested=spec.value_column,
        excluded={date_key},
    )
    observations: list[MacroObservation] = []
    for row in rows:
        observation_date = _parse_date(row.get(date_key))
        raw_value = row.get(value_key)
        if observation_date is None or raw_value in {None, ""}:
            continue
        observations.append(
            MacroObservation(
                series_key=spec.series_key,
                observation_date=observation_date,
                value=_numeric_text(str(raw_value)),
                unit=spec.unit,
                metadata={
                    "source": "RBI",
                    "source_page": RBI_DATA_RELEASES_PAGE,
                    "source_date_column": date_key,
                    "source_value_column": value_key,
                },
            )
        )
    return observations


def parse_nsdl_flows(data: bytes, media_type: str) -> list[MacroObservation]:
    rows = _records_from_payload(data, media_type)
    if not rows:
        return []

    date_key = _resolve_column(
        rows[0],
        requested=None,
        candidates=("date", "trade date", "report date", "as on", "day"),
    )
    fpi_key = _find_flow_column(rows[0], institution="fpi") or _find_flow_column(
        rows[0], institution="fii"
    )
    dii_key = _find_flow_column(rows[0], institution="dii")
    if not fpi_key and not dii_key:
        raise ValueError("NSDL payload did not contain an identifiable FPI/FII or DII net column")

    observations: list[MacroObservation] = []
    for row in rows:
        observation_date = _parse_date(row.get(date_key))
        if observation_date is None:
            continue
        for key, series_key in ((fpi_key, "fii_cash_net_cr"), (dii_key, "dii_cash_net_cr")):
            if not key or row.get(key) in {None, ""}:
                continue
            observations.append(
                MacroObservation(
                    series_key=series_key,
                    observation_date=observation_date,
                    value=_numeric_text(str(row[key])),
                    unit="INR cr",
                    metadata={
                        "source": "NSDL",
                        "source_page": NSDL_FPI_REPORTS_PAGE,
                        "source_column": key,
                    },
                )
            )
    return observations


def _records_from_payload(data: bytes, media_type: str) -> list[dict[str, str]]:
    normalized = media_type.split(";", 1)[0].strip().lower()
    if normalized in {"text/csv", "application/csv", "application/vnd.ms-excel", "text/plain"}:
        return _csv_records(data)
    if normalized in {"application/json", "text/json"}:
        return _json_records(data)
    if normalized in {"text/html", "application/xhtml+xml"}:
        return _html_records(data)
    raise ValueError(f"Unsupported official-source media type: {normalized}")


def _csv_records(data: bytes) -> list[dict[str, str]]:
    text_value = data.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text_value))
    return [_normalize_mapping(row) for row in reader if row]


def _json_records(data: bytes) -> list[dict[str, str]]:
    payload = json.loads(data.decode("utf-8-sig"))
    rows = _find_json_rows(payload)
    return [_normalize_mapping(row) for row in rows if isinstance(row, dict)]


def _find_json_rows(payload: object) -> list[dict[str, object]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("data", "rows", "table", "Table", "results", "result", "announcements"):
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
    for value in payload.values():
        if isinstance(value, list) and all(isinstance(item, dict) for item in value):
            return value
    return [payload]


def _html_records(data: bytes) -> list[dict[str, str]]:
    soup = BeautifulSoup(data, "lxml")
    records: list[dict[str, str]] = []
    for table in soup.find_all("table"):
        header_cells = table.find_all("th")
        headers = [_normalize_key(cell.get_text(" ", strip=True)) for cell in header_cells]
        if not headers:
            first_row = table.find("tr")
            if first_row:
                headers = [
                    _normalize_key(cell.get_text(" ", strip=True))
                    for cell in first_row.find_all(["td", "th"])
                ]
        if not headers:
            continue
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) != len(headers):
                continue
            record = {
                headers[index]: cell.get_text(" ", strip=True)
                for index, cell in enumerate(cells)
                if headers[index]
            }
            for index, cell in enumerate(cells):
                anchor = cell.find("a", href=True)
                if anchor and index < len(headers):
                    record[f"{headers[index]} url"] = str(anchor["href"])
            if record:
                records.append(record)
    return records


def _parse_bse_mobile_html(data: bytes, source_uri: str) -> list[OfficialDisclosureRecord]:
    soup = BeautifulSoup(data, "lxml")
    output: list[OfficialDisclosureRecord] = []
    for anchor in soup.find_all("a", href=True):
        href = str(anchor["href"])
        if "manndet.aspx" not in href.lower():
            continue
        absolute = urljoin(source_uri, href)
        query = parse_qs(urlparse(absolute).query)
        bse_code = _digits((query.get("scrip_CD") or query.get("scrip_cd") or [""])[0])
        text_value = " ".join(anchor.stripped_strings)
        company, headline, published = _split_bse_mobile_text(text_value)
        if not bse_code or not company or not headline:
            continue
        output.append(
            OfficialDisclosureRecord(
                exchange="BSE",
                bse_code=bse_code,
                company_name=company,
                headline=headline,
                published_at=_parse_datetime(published),
                source_uri=absolute,
                metadata={"official_page": source_uri},
            )
        )
    return output


def _split_bse_mobile_text(value: str) -> tuple[str | None, str | None, str | None]:
    cleaned = re.sub(r"\s+", " ", value).strip()
    match = re.match(
        r"^(?P<company>.+?)\s+-\s*(?P<headline>.+?)\s*,\s*"
        r"(?P<date>[A-Za-z]{3}\s+\d{1,2}\s+\d{4})\s*,\s*(?P<time>\d{1,2}:\d{2}\s*[AP]M)$",
        cleaned,
        re.I,
    )
    if not match:
        return None, None, None
    return (
        match.group("company").strip(),
        match.group("headline").strip(),
        f"{match.group('date')} {match.group('time')}",
    )


def _normalize_mapping(row: dict[object, object]) -> dict[str, str]:
    output: dict[str, str] = {}
    for key, value in row.items():
        if key is None:
            continue
        normalized = _normalize_key(str(key))
        if not normalized:
            continue
        output[normalized] = "" if value is None else str(value).strip()
    return output


def _normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.strip().lower()).strip()


def _first(row: dict[str, str], *keys: str) -> str | None:
    for key in keys:
        value = row.get(_normalize_key(key))
        if value is not None and value.strip():
            return value.strip()
    return None


def _absolute_url(value: str | None, base: str) -> str | None:
    if not value:
        return None
    candidate = value.strip()
    if not candidate or candidate.lower() in {"na", "n/a", "-", "--"}:
        return None
    if candidate.startswith("https://"):
        return candidate
    if candidate.startswith("http://"):
        return None
    if candidate.startswith("/") or "/" in candidate:
        return urljoin(base, candidate)
    return None


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    cleaned = re.sub(r"\s+", " ", value.strip())
    formats = (
        "%d-%b-%Y %H:%M:%S",
        "%d-%b-%Y %H:%M",
        "%d-%m-%Y %H:%M:%S",
        "%d/%m/%Y %H:%M:%S",
        "%b %d %Y %I:%M%p",
        "%b %d %Y %I:%M %p",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
    )
    for pattern in formats:
        try:
            parsed = datetime.strptime(cleaned, pattern)
            return parsed.replace(tzinfo=UTC)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        return None


def _parse_date(value: object) -> date | None:
    if value is None:
        return None
    cleaned = re.sub(r"\s+", " ", str(value).strip())
    for pattern in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%b %d %Y"):
        try:
            return datetime.strptime(cleaned, pattern).date()
        except ValueError:
            continue
    parsed = _parse_datetime(cleaned)
    return parsed.date() if parsed else None


def _digits(value: str | None) -> str | None:
    if not value:
        return None
    digits = re.sub(r"\D+", "", value)
    return digits or None


def _resolve_column(
    row: dict[str, str],
    *,
    requested: str | None,
    candidates: tuple[str, ...],
) -> str:
    if requested:
        key = _normalize_key(requested)
        if key not in row:
            raise ValueError(f"Requested column was not found: {requested}")
        return key
    for candidate in candidates:
        key = _normalize_key(candidate)
        if key in row:
            return key
    raise ValueError(f"Could not identify a required column from: {', '.join(candidates)}")


def _resolve_numeric_column(
    rows: list[dict[str, str]],
    *,
    requested: str | None,
    excluded: set[str],
) -> str:
    if requested:
        key = _normalize_key(requested)
        if key not in rows[0]:
            raise ValueError(f"Requested numeric column was not found: {requested}")
        return key

    preferred = ("value", "rate", "index", "amount", "net", "close", "price")
    for candidate in preferred:
        if candidate in rows[0] and candidate not in excluded:
            return candidate

    numeric_candidates: list[str] = []
    for key in rows[0]:
        if key in excluded:
            continue
        values = [row.get(key, "") for row in rows[:20]]
        populated = [value for value in values if str(value).strip()]
        if populated and sum(_looks_numeric(value) for value in populated) >= max(1, len(populated) // 2):
            numeric_candidates.append(key)
    if len(numeric_candidates) == 1:
        return numeric_candidates[0]
    raise ValueError("Could not unambiguously identify the macro value column")


def _find_flow_column(row: dict[str, str], *, institution: str) -> str | None:
    institution = institution.lower()
    for key in row:
        normalized = _normalize_key(key)
        if institution not in normalized:
            continue
        if "net" in normalized and any(term in normalized for term in ("equity", "cash", "investment")):
            return key
    for key in row:
        normalized = _normalize_key(key)
        if institution in normalized and "net" in normalized:
            return key
    return None


def _looks_numeric(value: object) -> bool:
    try:
        float(_numeric_text(str(value)))
        return True
    except ValueError:
        return False


def _numeric_text(value: str) -> str:
    cleaned = value.strip().replace(",", "")
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = f"-{cleaned[1:-1]}"
    cleaned = re.sub(r"[^0-9.+\-eE]", "", cleaned)
    if cleaned in {"", "+", "-", "."}:
        raise ValueError(f"Invalid numeric value: {value!r}")
    float(cleaned)
    return cleaned


def _dedupe_disclosures(
    records: list[OfficialDisclosureRecord],
) -> list[OfficialDisclosureRecord]:
    seen: set[tuple[str, str | None, str, str | None]] = set()
    output: list[OfficialDisclosureRecord] = []
    for record in records:
        identifier = record.nse_symbol or record.bse_code
        published = record.published_at.isoformat() if record.published_at else None
        key = (record.exchange, identifier, record.headline.strip().lower(), published)
        if key in seen:
            continue
        seen.add(key)
        output.append(record)
    return output
