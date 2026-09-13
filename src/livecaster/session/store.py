"""Session directory: state snapshots and append-only streams (SPEC §8, PLAN §5.6)."""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import time
import unicodedata
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from livecaster.config import ChannelConfig, Config
from livecaster.log import get_logger
from livecaster.outline.model import Outline
from livecaster.outline.parser import parse_outline_file
from livecaster.session.models import Event, Patch, Segment, Session
from livecaster.timeutil import atomic_write_json

log = get_logger(__name__)

SNAPSHOT_DEBOUNCE_S = 1.0
FORCE_SNAPSHOT_EVERY_S = 30.0


def slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    value = re.sub(r"[^\w\s-]", "", value).strip().lower()
    value = re.sub(r"[-\s]+", "-", value)
    return value or "session"


class JsonlWriter:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = path.open("a", encoding="utf-8")
        self._last_sync = time.monotonic()

    def write(self, obj: Any) -> None:
        self._fh.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")
        self._fh.flush()
        now = time.monotonic()
        if now - self._last_sync >= 5.0:
            try:
                import os

                os.fsync(self._fh.fileno())
            except OSError:  # pragma: no cover
                pass
            self._last_sync = now

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:  # pragma: no cover
            pass


class SessionStore:
    """Owns the session state, its files, and the broadcast fan-out."""

    def __init__(self, session: Session, directory: Path, outline: Outline, config: Config) -> None:
        self.session = session
        self.dir = directory
        self.outline = outline
        self.config = config
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "audio").mkdir(exist_ok=True)
        (self.dir / "final").mkdir(exist_ok=True)
        self._transcript_writer = JsonlWriter(self.dir / "transcript.jsonl")
        self._events_writer = JsonlWriter(self.dir / "events.jsonl")
        self._dirty = False
        self._last_snapshot = 0.0
        self._listeners: list[Callable[[str, Any], None]] = []
        self._snapshot_task: asyncio.Task[None] | None = None

    # --- paths -------------------------------------------------------------

    @property
    def state_path(self) -> Path:
        return self.dir / "session.json"

    @property
    def llm_log_path(self) -> Path:
        return self.dir / "llm.jsonl"

    @property
    def transcript_path(self) -> Path:
        return self.dir / "transcript.jsonl"

    @property
    def final_dir(self) -> Path:
        return self.dir / "final"

    # --- listeners ---------------------------------------------------------

    def subscribe(self, fn: Callable[[str, Any], None]) -> None:
        self._listeners.append(fn)

    def emit(self, kind: str, payload: Any) -> None:
        for fn in list(self._listeners):
            try:
                fn(kind, payload)
            except Exception:  # pragma: no cover - a broken listener must not stop the show
                log.exception("listener for %s failed", kind)

    # --- writes ------------------------------------------------------------

    def append_segment(self, segment: Segment) -> None:
        self._transcript_writer.write(segment.model_dump())
        self.mark_dirty()

    def log_event(self, kind: str, t: float, **data: Any) -> None:
        self._events_writer.write(Event(t=t, kind=kind, data=data).model_dump())

    def mark_dirty(self) -> None:
        self._dirty = True

    def snapshot(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force:
            if not self._dirty:
                return
            if now - self._last_snapshot < SNAPSHOT_DEBOUNCE_S:
                return
        atomic_write_json(self.state_path, self.session.model_dump(mode="json"))
        self._dirty = False
        self._last_snapshot = now

    async def snapshot_loop(self) -> None:
        """Debounced persistence: ≤ 1 s after a change, and at least every 30 s."""
        last_force = time.monotonic()
        try:
            while True:
                await asyncio.sleep(SNAPSHOT_DEBOUNCE_S)
                now = time.monotonic()
                force = now - last_force >= FORCE_SNAPSHOT_EVERY_S
                if force:
                    last_force = now
                self.snapshot(force=force)
        except asyncio.CancelledError:  # pragma: no cover
            self.snapshot(force=True)
            raise

    def apply_patch(self, patch: Patch) -> None:
        if patch.is_empty():
            return
        self.mark_dirty()
        self.emit("patch", patch)

    def close(self) -> None:
        self.snapshot(force=True)
        self._transcript_writer.close()
        self._events_writer.close()

    # --- outline reload ----------------------------------------------------

    def save_outline_copy(self, source: Path, index: int | None = None) -> Path:
        target = self.dir / ("outline.md" if index is None else f"outline.{index}.md")
        shutil.copyfile(source, target)
        return target


def create_session(
    outline_path: str | Path | None,
    config: Config,
    *,
    mode: str = "live",
    slug: str | None = None,
    base_dir: str | Path | None = None,
) -> SessionStore:
    """Open a session directory. ``outline_path`` may be None: the UI can upload one."""
    outline_file = Path(outline_path) if outline_path else None
    outline = parse_outline_file(outline_file) if outline_file else Outline()
    stem = slug or (outline_file.stem if outline_file else "session")
    day = datetime.now(UTC).astimezone().strftime("%Y-%m-%d")
    root = Path(base_dir or config.session.dir)
    directory = root / f"{day}_{slugify(stem)}"
    n = 2
    while directory.exists():
        directory = root / f"{day}_{slugify(stem)}-{n}"
        n += 1
    session = Session(
        id=directory.name,
        outline_path=str(outline_file.resolve()) if outline_file else "",
        mode=mode,  # type: ignore[arg-type]
        channels=[ChannelConfig(**c.model_dump()) for c in config.audio.channels],
        outline=outline.nodes,
        outline_next_id=outline.next_id,
        language=None if config.stt.language == "auto" else config.stt.language,
        target_minutes=config.session.target_minutes,
    )
    store = SessionStore(session, directory, outline, config)
    if outline_file:
        store.save_outline_copy(outline_file)
    store.snapshot(force=True)
    store.log_event("created", 0.0, outline=str(outline_file or ""), mode=mode)
    return store


def load_session(directory: str | Path, config: Config | None = None) -> SessionStore:
    directory = Path(directory)
    state = json.loads((directory / "session.json").read_text(encoding="utf-8"))
    session = Session.model_validate(state)
    outline = Outline(
        nodes=session.outline, source_path=session.outline_path, next_id=session.outline_next_id
    )
    cfg = config or Config()
    return SessionStore(session, directory, outline, cfg)
