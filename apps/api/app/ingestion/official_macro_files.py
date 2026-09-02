from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from urllib.parse import urlparse

from app.ingestion.macro import MacroObservation, canonical_macro_series_key

RBI_IMPORT_SERIES = frozenset(
    {
        "repo_rate",
        "india_10y_yield",
        "usd_inr",
        "cpi_yoy",
        "iip_yoy",
    }
)
NSDL_IMPORT_SERIES = frozenset({"fii_cash_net_cr", "dii_cash_net_cr"})

_PROVIDER_HOSTS = {
    "RBI": frozenset({"rbi.org.in", "statistics.rbi.org.in"}),
    "NSDL": frozenset({"nsdl.co.in", "fpi.nsdl.co.in", "pilot.fpi.nsdl.co.in"}),
}
_EXTENSION_MEDIA_TYPES = {
    ".csv": "text/csv",
    ".json": "application/json",
    ".html": "text/html",
    ".htm": "text/html",
    ".txt": "text/plain",
}
_SUPPORTED_MEDIA_TYPES = frozenset(_EXTENSION_MEDIA_TYPES.values())


def validate_official_source_url(provider: str, source_url: str) -> str:
    normalized_provider = provider.strip().upper()
    allowed_hosts = _PROVIDER_HOSTS.get(normalized_provider)
    if allowed_hosts is None:
        raise ValueError("provider must be RBI or NSDL")

    candidate = source_url.strip()
    parsed = urlparse(candidate)
    hostname = (parsed.hostname or "").lower().strip(".")
    if parsed.scheme != "https" or not any(
        hostname == host or hostname.endswith(f".{host}") for host in allowed_hosts
    ):
        raise ValueError(
            f"{normalized_provider} source URL must use HTTPS on an approved official domain"
        )
    return candidate


def validate_rbi_series_key(series_key: str) -> str:
    normalized = canonical_macro_series_key(series_key)
    if normalized not in RBI_IMPORT_SERIES:
        allowed = ", ".join(sorted(RBI_IMPORT_SERIES))
        raise ValueError(f"RBI series_key must be one of: {allowed}")
    return normalized


def resolve_media_type(path: Path, explicit_media_type: str | None = None) -> str:
    if explicit_media_type:
        normalized = explicit_media_type.split(";", 1)[0].strip().lower()
        if normalized not in _SUPPORTED_MEDIA_TYPES:
            allowed = ", ".join(sorted(_SUPPORTED_MEDIA_TYPES))
            raise ValueError(f"Unsupported media type {normalized!r}; allowed: {allowed}")
        return normalized

    media_type = _EXTENSION_MEDIA_TYPES.get(path.suffix.lower())
    if media_type is None:
        raise ValueError(
            "Unable to infer media type; use a .csv/.json/.html/.htm/.txt file or pass --media-type"
        )
    return media_type


def validate_macro_observations(
    observations: Iterable[MacroObservation],
    *,
    allowed_series: frozenset[str],
    min_rows: int,
) -> list[MacroObservation]:
    if min_rows < 1:
        raise ValueError("min_rows must be >= 1")

    items = list(observations)
    if len(items) < min_rows:
        raise ValueError(
            f"Official macro export contains only {len(items)} usable rows; minimum expected is {min_rows}"
        )

    seen: set[tuple[str, object]] = set()
    duplicate_keys: set[str] = set()
    for item in items:
        series_key = canonical_macro_series_key(item.series_key)
        if series_key not in allowed_series:
            raise ValueError(f"Unexpected macro series in official export: {series_key}")
        natural_key = (series_key, item.observation_date)
        if natural_key in seen:
            duplicate_keys.add(f"{series_key}:{item.observation_date.isoformat()}")
        else:
            seen.add(natural_key)

    if duplicate_keys:
        preview = ", ".join(sorted(duplicate_keys)[:10])
        raise ValueError(f"Official macro export contains duplicate natural keys: {preview}")
    return items
