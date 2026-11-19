"""Audit logging with redaction.

Logs go to a file and to stderr. Stdout is never used: it carries MCP protocol
messages when Guardian runs as a STDIO server.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

_SENSITIVE_KEY = re.compile(
    r"(token|secret|password|passwd|authorization|api[_-]?key|credential|"
    r"cookie|body|content|snippet|text|html|message|subject|to|cc|bcc)",
    re.IGNORECASE,
)
_MAX_STRING = 80
REDACTED = "[REDACTED]"


def redact(value: Any, key: str | None = None) -> Any:
    """Return a copy of ``value`` that is safe to write to logs."""
    if key is not None and _SENSITIVE_KEY.search(key):
        return REDACTED
    if isinstance(value, dict):
        return {str(k): redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str) and len(value) > _MAX_STRING:
        return value[:_MAX_STRING] + f"...[{len(value)} chars]"
    return value


def summarize_arguments(arguments: dict[str, Any]) -> str:
    return json.dumps(redact(arguments), ensure_ascii=False, sort_keys=True)


def setup_logging(log_file: Path | None, level: int = logging.INFO) -> logging.Logger:
    root = logging.getLogger("guardian")
    root.setLevel(level)
    root.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    stderr_handler = logging.StreamHandler()  # stderr by default
    stderr_handler.setFormatter(fmt)
    root.addHandler(stderr_handler)

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)

    root.propagate = False
    return root
