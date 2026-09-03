"""Build ID for the UI, so an open tab reloads itself after a code change (FR-27)."""

from __future__ import annotations

import hashlib
from pathlib import Path

UI_DIR = Path(__file__).resolve().parents[1] / "ui"


def compute_build_id(ui_dir: Path | None = None) -> str:
    """Hash of every file under ``ui/``; changes whenever the front end changes."""
    directory = ui_dir or UI_DIR
    h = hashlib.sha256()
    for path in sorted(p for p in directory.rglob("*") if p.is_file()):
        h.update(path.relative_to(directory).as_posix().encode())
        h.update(path.read_bytes())
    return h.hexdigest()[:12]


def index_html(build_id: str, theme: str = "dark", ui_dir: Path | None = None) -> str:
    directory = ui_dir or UI_DIR
    html = (directory / "index.html").read_text(encoding="utf-8")
    return html.replace("{{BUILD_ID}}", build_id).replace("{{THEME}}", theme)
