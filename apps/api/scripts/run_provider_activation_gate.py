from __future__ import annotations

import json

from app.core.config import get_settings
from app.core.provider_activation import evaluate_provider_activation


def main() -> int:
    report = evaluate_provider_activation(get_settings())
    print(json.dumps(report.as_dict(), indent=2, sort_keys=True, default=str))
    return 0 if report.ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
