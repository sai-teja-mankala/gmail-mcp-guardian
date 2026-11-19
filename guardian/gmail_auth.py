"""One-time Gmail authorization.

Run from the repository root:  python -m guardian.gmail_auth
"""

from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

from guardian.gmail import TOKEN_PATH, authorize

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    load_dotenv(ROOT / ".env")
    authorize()
    print(f"Gmail authorization saved to {TOKEN_PATH}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
