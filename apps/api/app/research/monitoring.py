from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine


@dataclass(frozen=True)
class SnapshotDelta:
    baseline_available: bool
    changed: bool
    thesis_changed: bool
    severity: str
    added_catalysts: tuple[dict[str, Any], ...]
    resolved_catalysts: tuple[dict[str, Any], ...]
    added_risks: tuple[dict[str, Any], ...]
    resolved_risks: tuple[dict[str, Any], ...]
    confidence_changes: tuple[dict[str, object], ...]
    metric_changes: tuple[dict[str, object], ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "baseline_available": self.baseline_available,
            "changed": self.changed,
            "thesis_changed": self.thesis_changed,
            "severity": self.severity,
            "added_catalysts": list(self.added_catalysts),
            "resolved_catalysts": list(self.resolved_catalysts),
            "added_risks": list(self.added_risks),
            "resolved_risks": list(self.resolved_risks),
            "confidence_changes": list(self.confidence_changes),
            "metric_changes": list(self.metric_changes),
        }


def thesis_hash(report: Mapping[str, Any]) -> str:
    """Hash only validated thesis-bearing report content, never timestamps or display noise."""
    sections = _mapping(report.get("sections"))
    payload = {
        "executive_summary": report.get("executive_summary"),
        "bull_case": report.get("bull_case"),
        "bear_case": report.get("bear_case"),
        "watch_items": report.get("watch_items"),
        "thesis_breakers": report.get("thesis_breakers"),
        "financials": sections.get("financials", []),
        "earnings": sections.get("earnings", []),
        "valuation": sections.get("valuation", []),
        "risk": sections.get("risk", []),
        "industry": sections.get("industry", []),
        "macro": sections.get("macro", []),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def compare_snapshots(previous: Mapping[str, Any] | None, current: Mapping[str, Any]) -> SnapshotDelta:
    if previous is None:
        return SnapshotDelta(
            baseline_available=False,
            changed=False,
            thesis_changed=False,
            severity="info",
            added_catalysts=(),
            resolved_catalysts=(),
            added_risks=(),
            resolved_risks=(),
            confidence_changes=(),
            metric_changes=(),
        )

    previous_catalysts = _claim_list(previous.get("catalysts"))
    current_catalysts = _claim_list(current.get("catalysts"))
    previous_risks = _claim_list(previous.get("risks"))
    current_risks = _claim_list(current.get("risks"))

    added_catalysts = tuple(_new_items(current_catalysts, previous_catalysts))
    resolved_catalysts = tuple(_new_items(previous_catalysts, current_catalysts))
    added_risks = tuple(_new_items(current_risks, previous_risks))
    resolved_risks = tuple(_new_items(previous_risks, current_risks))

    previous_metrics = _mapping(previous.get("metrics"))
    current_metrics = _mapping(current.get("metrics"))
    confidence_changes = tuple(
        _numeric_changes(
            _mapping(previous_metrics.get("confidence")),
            _mapping(current_metrics.get("confidence")),
            relative_threshold=0.0,
            absolute_threshold=0.025,
            limit=8,
        )
    )
    metric_changes = tuple(
        _numeric_changes(
            _without_confidence(previous_metrics),
            _without_confidence(current_metrics),
            relative_threshold=0.05,
            absolute_threshold=1e-9,
            limit=40,
        )
    )
    thesis_changed = bool(
        previous.get("thesis_hash")
        and current.get("thesis_hash")
        and previous.get("thesis_hash") != current.get("thesis_hash")
    )

    changed = bool(
        thesis_changed
        or added_catalysts
        or resolved_catalysts
        or added_risks
        or resolved_risks
        or confidence_changes
        or metric_changes
    )
    severity = _severity(
        thesis_changed=thesis_changed,
        added_risks=added_risks,
        resolved_risks=resolved_risks,
        added_catalysts=added_catalysts,
        confidence_changes=confidence_changes,
        metric_changes=metric_changes,
    )
    return SnapshotDelta(
        baseline_available=True,
        changed=changed,
        thesis_changed=thesis_changed,
        severity=severity,
        added_catalysts=added_catalysts,
        resolved_catalysts=resolved_catalysts,
        added_risks=added_risks,
        resolved_risks=resolved_risks,
        confidence_changes=confidence_changes,
        metric_changes=metric_changes,
    )


def delta_summary(delta: SnapshotDelta) -> str:
    parts: list[str] = []
    if delta.thesis_changed:
        parts.append("validated thesis changed")
    if delta.added_risks:
        parts.append(f"{len(delta.added_risks)} new risk(s)")
    if delta.resolved_risks:
        parts.append(f"{len(delta.resolved_risks)} resolved risk(s)")
    if delta.added_catalysts:
        parts.append(f"{len(delta.added_catalysts)} new catalyst(s)")
    if delta.resolved_catalysts:
        parts.append(f"{len(delta.resolved_catalysts)} resolved catalyst(s)")
    if delta.confidence_changes:
        parts.append(f"{len(delta.confidence_changes)} confidence change(s)")
    if delta.metric_changes:
        parts.append(f"{len(delta.metric_changes)} material metric change(s)")
    return "; ".join(parts) if parts else "No material validated change detected"


class MonitoringRepository:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def latest_snapshots_for_user(
        self,
        user_id: UUID,
        security_id: UUID,
        *,
        limit: int = 2,
    ) -> list[dict[str, Any]]:
        statement = text(
            """
            select s.*
            from analysis_snapshots s
            join research_jobs j on j.id = s.job_id
            where s.security_id = :security_id
              and j.requested_by = :user_id
              and j.status = 'completed'
            order by s.snapshot_at desc, s.id desc
            limit :limit
            """
        )
        async with self.engine.connect() as connection:
            rows = (
                await connection.execute(
                    statement,
                    {"user_id": user_id, "security_id": security_id, "limit": max(1, min(limit, 10))},
                )
            ).mappings().all()
        return [dict(row) for row in rows]

    async def latest_delta_for_user(
        self,
        user_id: UUID,
        security_id: UUID,
    ) -> dict[str, object]:
        snapshots = await self.latest_snapshots_for_user(user_id, security_id, limit=2)
        if not snapshots:
            return {"security_id": str(security_id), "snapshot": None, "delta": None}
        current = snapshots[0]
        previous = snapshots[1] if len(snapshots) > 1 else None
        delta = compare_snapshots(previous, current)
        return {
            "security_id": str(security_id),
            "snapshot": _public_snapshot(current),
            "previous_snapshot": _public_snapshot(previous) if previous else None,
            "delta": delta.as_dict(),
            "summary": delta_summary(delta),
        }

    async def list_alerts(
        self,
        user_id: UUID,
        *,
        unread_only: bool = False,
        limit: int = 50,
    ) -> list[dict[str, object]]:
        statement = text(
            """
            select a.id, a.security_id, a.source_snapshot_id, a.prior_snapshot_id, a.job_id,
                   a.severity, a.summary, a.delta, a.read_at, a.created_at,
                   s.legal_name, s.nse_symbol, s.bse_code, s.sector, s.industry
            from monitoring_alerts a
            join securities s on s.id = a.security_id
            where a.user_id = :user_id
              and (:unread_only = false or a.read_at is null)
            order by a.created_at desc, a.id desc
            limit :limit
            """
        )
        async with self.engine.connect() as connection:
            rows = (
                await connection.execute(
                    statement,
                    {
                        "user_id": user_id,
                        "unread_only": unread_only,
                        "limit": max(1, min(limit, 200)),
                    },
                )
            ).mappings().all()
        return [_jsonable_row(dict(row)) for row in rows]

    async def mark_read(self, user_id: UUID, alert_id: UUID) -> bool:
        statement = text(
            """
            update monitoring_alerts
            set read_at = coalesce(read_at, now())
            where id = :alert_id and user_id = :user_id
            returning id
            """
        )
        async with self.engine.begin() as connection:
            value = await connection.scalar(statement, {"alert_id": alert_id, "user_id": user_id})
        return value is not None

    async def create_alert_for_snapshot(
        self,
        *,
        connection: AsyncConnection,
        user_id: UUID,
        security_id: UUID,
        job_id: UUID,
        source_snapshot_id: UUID,
    ) -> UUID | None:
        rows = (
            await connection.execute(
                text(
                    """
                    select s.*
                    from analysis_snapshots s
                    join research_jobs j on j.id = s.job_id
                    where s.security_id = :security_id
                      and j.requested_by = :user_id
                      and s.id <> :source_snapshot_id
                      and s.snapshot_at <= (
                        select snapshot_at from analysis_snapshots where id = :source_snapshot_id
                      )
                    order by s.snapshot_at desc, s.id desc
                    limit 1
                    """
                ),
                {
                    "security_id": security_id,
                    "user_id": user_id,
                    "source_snapshot_id": source_snapshot_id,
                },
            )
        ).mappings().all()
        if not rows:
            return None
        previous = dict(rows[0])
        current_row = (
            await connection.execute(
                text("select * from analysis_snapshots where id = :id"),
                {"id": source_snapshot_id},
            )
        ).mappings().one()
        current = dict(current_row)
        delta = compare_snapshots(previous, current)
        if not delta.changed:
            return None
        result = await connection.execute(
            text(
                """
                insert into monitoring_alerts (
                    user_id, security_id, source_snapshot_id, prior_snapshot_id,
                    job_id, severity, summary, delta
                ) values (
                    :user_id, :security_id, :source_snapshot_id, :prior_snapshot_id,
                    :job_id, :severity, :summary, cast(:delta as jsonb)
                )
                on conflict (user_id, source_snapshot_id) do nothing
                returning id
                """
            ),
            {
                "user_id": user_id,
                "security_id": security_id,
                "source_snapshot_id": source_snapshot_id,
                "prior_snapshot_id": previous["id"],
                "job_id": job_id,
                "severity": delta.severity,
                "summary": delta_summary(delta),
                "delta": json.dumps(delta.as_dict(), default=str),
            },
        )
        value = result.scalar_one_or_none()
        return UUID(str(value)) if value is not None else None


def _severity(
    *,
    thesis_changed: bool,
    added_risks: Sequence[object],
    resolved_risks: Sequence[object],
    added_catalysts: Sequence[object],
    confidence_changes: Sequence[Mapping[str, object]],
    metric_changes: Sequence[Mapping[str, object]],
) -> str:
    if thesis_changed and (added_risks or len(confidence_changes) >= 2):
        return "high"
    if thesis_changed or added_risks or added_catalysts or resolved_risks:
        return "material"
    if confidence_changes or metric_changes:
        return "info"
    return "info"


def _numeric_changes(
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
    *,
    relative_threshold: float,
    absolute_threshold: float,
    limit: int,
) -> list[dict[str, object]]:
    old = _flatten_numbers(previous)
    new = _flatten_numbers(current)
    changes: list[dict[str, object]] = []
    for key in sorted(set(old) & set(new)):
        before = old[key]
        after = new[key]
        absolute = after - before
        denominator = abs(before)
        relative = abs(absolute) / denominator if denominator > 1e-12 else None
        material = abs(absolute) >= absolute_threshold and (
            relative_threshold <= 0
            or relative is None
            or relative >= relative_threshold
        )
        if not material:
            continue
        changes.append(
            {
                "metric": key,
                "previous": before,
                "current": after,
                "absolute_change": absolute,
                "relative_change": relative,
            }
        )
        if len(changes) >= limit:
            break
    return changes


def _flatten_numbers(value: Mapping[str, Any], prefix: str = "") -> dict[str, float]:
    flattened: dict[str, float] = {}
    for key, item in value.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(item, bool):
            continue
        if isinstance(item, (int, float)):
            flattened[path] = float(item)
        elif isinstance(item, Mapping):
            flattened.update(_flatten_numbers(item, path))
    return flattened


def _new_items(current: list[dict[str, Any]], previous: list[dict[str, Any]]) -> list[dict[str, Any]]:
    previous_keys = {_claim_key(item) for item in previous}
    return [item for item in current if _claim_key(item) not in previous_keys]


def _claim_key(item: Mapping[str, Any]) -> str:
    for field in ("claim_id", "metric", "event_id", "source_event_id"):
        value = item.get(field)
        if value:
            return f"{field}:{value}"
    data = _mapping(item.get("data"))
    for field in ("event_id", "metric", "source_event_id"):
        value = data.get(field)
        if value:
            return f"data.{field}:{value}"
    statement = " ".join(str(item.get("statement") or "").lower().split())
    return f"statement:{statement}"


def _claim_list(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _without_confidence(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != "confidence"}


def _public_snapshot(snapshot: Mapping[str, Any]) -> dict[str, object]:
    return {
        "id": str(snapshot["id"]),
        "job_id": str(snapshot["job_id"]) if snapshot.get("job_id") else None,
        "snapshot_type": snapshot.get("snapshot_type"),
        "snapshot_at": _iso(snapshot.get("snapshot_at")),
        "thesis_hash": snapshot.get("thesis_hash"),
        "metrics": snapshot.get("metrics") if isinstance(snapshot.get("metrics"), dict) else {},
        "catalysts": _claim_list(snapshot.get("catalysts")),
        "risks": _claim_list(snapshot.get("risks")),
        "metadata": snapshot.get("metadata") if isinstance(snapshot.get("metadata"), dict) else {},
    }


def _jsonable_row(row: Mapping[str, Any]) -> dict[str, object]:
    return {
        key: (str(value) if isinstance(value, UUID) else _iso(value) if hasattr(value, "isoformat") else value)
        for key, value in row.items()
    }


def _iso(value: object) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    return str(value)
