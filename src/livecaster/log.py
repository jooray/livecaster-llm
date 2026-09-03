"""Logging setup: rich to stderr, plus an optional file inside the session directory."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

console = Console(stderr=True)

_SECRET_RE = re.compile(r"(sk-[A-Za-z0-9_\-]{8,}|Bearer\s+[A-Za-z0-9_\-\.]{8,})")


class SecretFilter(logging.Filter):
    """Belt and braces: never let an API key reach a log line."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _SECRET_RE.sub("<redacted>", record.msg)
        if record.args:
            try:
                record.args = tuple(
                    _SECRET_RE.sub("<redacted>", a) if isinstance(a, str) else a for a in record.args
                )
            except TypeError:  # dict-style args
                pass
        return True


_configured = False


def setup_logging(level: str = "INFO") -> None:
    global _configured
    if _configured:
        return
    handler = RichHandler(console=console, rich_tracebacks=True, show_path=False, markup=False)
    handler.addFilter(SecretFilter())
    logging.basicConfig(level=level, format="%(message)s", datefmt="%H:%M:%S", handlers=[handler])
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("watchfiles").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    _configured = True


def add_session_log(session_dir: Path, level: str = "DEBUG") -> None:
    """Attach a file handler writing into the session directory."""
    session_dir.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(session_dir / "livecaster.log", encoding="utf-8")
    fh.setLevel(level)
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    fh.addFilter(SecretFilter())
    logging.getLogger().addHandler(fh)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
