from __future__ import annotations

import re
from collections.abc import Iterable

from rapidfuzz import fuzz

from app.securities.models import ResolveCandidate, ResolveResult, SecurityRecord


def normalize_security_query(value: str) -> str:
    normalized = re.sub(r"[^A-Z0-9]+", " ", value.upper()).strip()
    suffixes = {"LTD", "LIMITED", "INDIA", "THE"}
    parts = [part for part in normalized.split() if part not in suffixes]
    return " ".join(parts)


class SecurityResolver:
    """Deterministic resolver over the India security master.

    Source-of-truth records should be populated from exchange/security-master ingestion.
    The resolver itself contains no hard-coded current index memberships.
    """

    def __init__(self, securities: Iterable[SecurityRecord]) -> None:
        self.securities = list(securities)

    def resolve(self, query: str, *, threshold: float = 0.82) -> ResolveResult:
        normalized = normalize_security_query(query)
        candidates: list[ResolveCandidate] = []

        for security in self.securities:
            identifiers = {
                security.legal_name: "legal_name",
                security.nse_symbol or "": "nse_symbol",
                security.bse_code or "": "bse_code",
                security.isin or "": "isin",
                **{alias: "alias" for alias in security.aliases},
            }
            best_score = 0.0
            best_reason = "fuzzy"
            for identifier, reason in identifiers.items():
                if not identifier:
                    continue
                candidate_key = normalize_security_query(identifier)
                if normalized == candidate_key:
                    best_score = 1.0
                    best_reason = reason
                    break
                score = fuzz.WRatio(normalized, candidate_key) / 100.0
                if score > best_score:
                    best_score = score
                    best_reason = f"fuzzy_{reason}"

            candidates.append(
                ResolveCandidate(
                    security=security,
                    score=best_score,
                    match_reason=best_reason,
                )
            )

        candidates.sort(key=lambda candidate: candidate.score, reverse=True)
        top = candidates[0] if candidates else None
        resolved = bool(top and top.score >= threshold)

        return ResolveResult(
            query=query,
            normalized_query=normalized,
            resolved=resolved,
            candidate=top if resolved else None,
            alternatives=candidates[:5],
        )
