from app.securities.models import SecurityRecord
from app.securities.resolver import SecurityResolver, normalize_security_query


def test_normalization_removes_common_legal_suffixes() -> None:
    assert normalize_security_query("Example Industries Limited") == "EXAMPLE INDUSTRIES"


def test_resolves_exact_nse_symbol() -> None:
    resolver = SecurityResolver(
        [
            SecurityRecord(
                legal_name="Example Industries Limited",
                nse_symbol="EXAMPLE",
                isin="INE000000001",
                aliases=["Example Industries"],
            )
        ]
    )

    result = resolver.resolve("EXAMPLE")

    assert result.resolved is True
    assert result.candidate is not None
    assert result.candidate.security.nse_symbol == "EXAMPLE"
    assert result.candidate.score == 1.0


def test_does_not_force_low_confidence_match() -> None:
    resolver = SecurityResolver(
        [SecurityRecord(legal_name="Example Industries Limited", nse_symbol="EXAMPLE")]
    )

    result = resolver.resolve("totally unrelated security", threshold=0.9)

    assert result.resolved is False
    assert result.candidate is None
