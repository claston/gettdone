from __future__ import annotations

import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    backend_root = Path(__file__).resolve().parents[1]
    if str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))

    from app.cli.ai_recovery_benchmark import main as cli_main

    return cli_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
