from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import unquote, urlparse
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

_SYNTHETIC_SOURCE_TOKENS = frozenset(
    {
        "synthetic",
        "mock",
        "fake",
        "dummy",
        "fixture",
        "sample",
        "generated",
        "placeholder",
    }
)

# Domains controlled by exchanges, regulators, depositories, statistical authorities,
# or public central-bank data services. Subdomains are accepted.
_OFFICIAL_SOURCE_HOST_SUFFIXES = (
    "nseindia.com",
    "bseindia.com",
    "sebi.gov.in",
    "rbi.org.in",
    "nsdl.co.in",
    "cdslindia.com",
    "mospi.gov.in",
    "stlouisfed.org",
)


@dataclass(frozen=True)
class ReferenceApproval:
    provenance_class: str
    approval_reference: str | None
    source_host: str | None

    def as_metadata(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "production_approved": True,
            "provenance_class": self.provenance_class,
        }
        if self.source_host:
            payload["source_host"] = self.source_host
        if self.approval_reference:
            payload["approval_reference"] = self.approval_reference
        return payload


async def resolve_security(engine: AsyncEngine, identifier: str) -> tuple[UUID, str]:
    lookup = identifier.strip()
    if not lookup:
        raise ValueError("security identifier cannot be empty")
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    """
                    select id, legal_name
                    from securities
                    where upper(coalesce(nse_symbol, '')) = upper(:identifier)
                       or bse_code = :identifier
                       or upper(coalesce(isin, '')) = upper(:identifier)
                    order by legal_name
                    limit 2
                    """
                ),
                {"identifier": lookup},
            )
        ).mappings().all()
    if not rows:
        raise ValueError(f"security not found in canonical master: {lookup}")
    if len(rows) > 1:
        raise ValueError(f"security identifier is ambiguous: {lookup}")
    return UUID(str(rows[0]["id"])), str(rows[0]["legal_name"])


def validate_source_uri(value: str) -> str:
    cleaned = value.strip()
    parsed = urlparse(cleaned)
    if not parsed.scheme:
        raise ValueError("source_uri must be an absolute URI with a scheme")
    if parsed.username or parsed.password:
        raise ValueError("source_uri must not contain embedded credentials")

    normalized_scheme = parsed.scheme.strip().lower()
    if normalized_scheme in _SYNTHETIC_SOURCE_TOKENS:
        raise ValueError("synthetic/mock/sample source URIs are not permitted")

    provenance_text = unquote(
        " ".join(
            part
            for part in (parsed.netloc, parsed.path, parsed.params, parsed.query, parsed.fragment)
            if part
        )
    ).lower()
    _reject_synthetic_tokens(provenance_text, label="source_uri")
    return cleaned


def validate_provider_name(value: str) -> str:
    cleaned = value.strip().lower()
    if not cleaned:
        raise ValueError("provider cannot be empty")
    _reject_synthetic_tokens(cleaned, label="provider")
    return cleaned


def validate_reference_approval(
    source_uri: str,
    approval_reference: str | None = None,
) -> ReferenceApproval:
    cleaned_uri = validate_source_uri(source_uri)
    parsed = urlparse(cleaned_uri)
    host = (parsed.hostname or "").strip().lower() or None
    if host and parsed.scheme.lower() in {"http", "https"} and _is_official_host(host):
        return ReferenceApproval(
            provenance_class="official_source",
            approval_reference=None,
            source_host=host,
        )

    approval = (approval_reference or "").strip()
    if not approval:
        raise ValueError(
            "non-official reference data requires --approval-reference identifying the "
            "license, contract, internal approval, or source-governance record"
        )
    if len(approval) > 240:
        raise ValueError("approval reference must be 240 characters or fewer")
    _reject_synthetic_tokens(approval.lower(), label="approval reference")
    return ReferenceApproval(
        provenance_class="licensed_or_approved",
        approval_reference=approval,
        source_host=host,
    )


def parse_optional_datetime(value: str | None) -> datetime | None:
    cleaned = (value or "").strip()
    if not cleaned:
        return None
    parsed = datetime.fromisoformat(cleaned)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


async def upsert_reference_source(
    engine: AsyncEngine,
    *,
    security_id: UUID | None,
    source_type: str,
    source_uri: str,
    title: str,
    published_at: datetime | None,
    checksum: str,
    metadata: dict[str, object],
    approval_reference: str | None = None,
) -> UUID:
    source_type = source_type.strip().lower()
    if not source_type:
        raise ValueError("source_type cannot be empty")
    approval = validate_reference_approval(source_uri, approval_reference)
    audited_metadata = {**metadata, **approval.as_metadata()}
    parameters = {
        "security_id": security_id,
        "source_type": source_type,
        "source_uri": validate_source_uri(source_uri),
        "title": title.strip() or None,
        "published_at": published_at,
        "checksum": checksum,
        "metadata": json.dumps(audited_metadata),
    }
    async with engine.begin() as connection:
        source_id = await connection.scalar(
            text(
                """
                insert into sources (
                    security_id, source_type, source_uri, title, published_at,
                    freshness, checksum, metadata
                ) values (
                    :security_id, :source_type, :source_uri, :title, :published_at,
                    'historical', :checksum, cast(:metadata as jsonb)
                )
                on conflict do nothing
                returning id
                """
            ),
            parameters,
        )
        if source_id is None:
            source_id = await connection.scalar(
                text(
                    """
                    select id
                    from sources
                    where security_id is not distinct from :security_id
                      and source_uri = :source_uri
                      and coalesce(published_at, '1970-01-01 00:00:00+00'::timestamptz)
                          = coalesce(
                              :published_at,
                              '1970-01-01 00:00:00+00'::timestamptz
                            )
                    limit 1
                    """
                ),
                parameters,
            )
            if source_id is None:
                raise RuntimeError("unable to resolve reference source after upsert")
            await connection.execute(
                text(
                    """
                    update sources
                    set source_type = :source_type,
                        title = :title,
                        checksum = :checksum,
                        metadata = cast(:metadata as jsonb),
                        retrieved_at = now()
                    where id = :source_id
                    """
                ),
                {**parameters, "source_id": source_id},
            )
    return UUID(str(source_id))


async def upsert_restricted_external_source(
    engine: AsyncEngine,
    *,
    security_id: UUID,
    source_type: str,
    source_uri: str,
    title: str,
    published_at: datetime | None,
    checksum: str,
    freshness: str,
    metadata: dict[str, object],
) -> UUID:
    """Persist an external source that is allowed for internal research only."""
    normalized_type = source_type.strip().lower()
    if not normalized_type or normalized_type.startswith("reference_"):
        raise ValueError("restricted source_type must be non-empty and cannot use reference_*")
    normalized_freshness = freshness.strip().lower()
    if normalized_freshness not in {"near_live", "periodic", "historical"}:
        raise ValueError("restricted source freshness must be near_live, periodic, or historical")
    parameters = {
        "security_id": security_id,
        "source_type": normalized_type,
        "source_uri": validate_source_uri(source_uri),
        "title": title.strip() or None,
        "published_at": published_at,
        "freshness": normalized_freshness,
        "checksum": checksum,
        "metadata": json.dumps(
            {
                **metadata,
                "production_approved": False,
                "commercial_display_approved": False,
                "allowed_use": "internal_research",
                "provenance_class": "restricted_external_source",
                "licensing_status": "not_approved_for_commercial_display",
            }
        ),
    }
    async with engine.begin() as connection:
        source_id = await connection.scalar(
            text(
                """
                insert into sources (
                    security_id, source_type, source_uri, title, published_at,
                    retrieved_at, freshness, checksum, metadata
                ) values (
                    :security_id, :source_type, :source_uri, :title, :published_at,
                    now(), :freshness, :checksum, cast(:metadata as jsonb)
                )
                on conflict do nothing
                returning id
                """
            ),
            parameters,
        )
        if source_id is None:
            source_id = await connection.scalar(
                text(
                    """
                    select id from sources
                    where security_id=:security_id
                      and source_uri=:source_uri
                      and coalesce(published_at, '1970-01-01 00:00:00+00'::timestamptz)
                        = coalesce(:published_at, '1970-01-01 00:00:00+00'::timestamptz)
                    limit 1
                    """
                ),
                parameters,
            )
        if source_id is None:
            raise RuntimeError("unable to resolve restricted source after upsert")
    return UUID(str(source_id))


def _is_official_host(host: str) -> bool:
    return any(host == suffix or host.endswith(f".{suffix}") for suffix in _OFFICIAL_SOURCE_HOST_SUFFIXES)


def _reject_synthetic_tokens(value: str, *, label: str) -> None:
    tokens = {token for token in re.split(r"[^a-z0-9]+", value) if token}
    blocked = sorted(tokens & _SYNTHETIC_SOURCE_TOKENS)
    if blocked:
        raise ValueError(
            f"{label} appears to identify non-production data: " + ", ".join(blocked)
        )
