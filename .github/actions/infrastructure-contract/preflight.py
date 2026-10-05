from __future__ import annotations

import os
import sys


def missing_reader_inputs(values: dict[str, str]) -> list[str]:
    return [
        name for name in ("INFRA_READER_APP_ID", "READER_APP_PRIVATE_KEY")
        if not values.get(name, "").strip()
    ]


def main(argv: list[str] | None = None) -> int:
    del argv
    missing = missing_reader_inputs(dict(os.environ))
    if missing:
        print(
            "Infrastructure application checkout is Not Assessed. Configure "
            + ", ".join(missing)
            + " at the authoritative repository/protected environment. "
            "Use a selected-repository Contents-read App; no token fallback or permission widening is permitted.",
            file=sys.stderr,
        )
        return 1
    print("Application-read credential inputs are present; installation validity remains Not Assessed until mint/checkout succeeds.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
