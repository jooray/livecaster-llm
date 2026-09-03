"""Input device enumeration (FR-06)."""

from __future__ import annotations

import platform
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any


@dataclass
class DeviceInfo:
    index: int
    name: str
    max_input_channels: int
    default_samplerate: float
    is_default: bool = False
    hostapi: str = ""


def list_devices() -> list[DeviceInfo]:
    import sounddevice as sd

    default_in = None
    try:
        default_in = sd.default.device[0]
    except Exception:  # pragma: no cover
        pass
    hostapis = sd.query_hostapis()
    out: list[DeviceInfo] = []
    for i, d in enumerate(sd.query_devices()):
        if int(d["max_input_channels"]) <= 0:
            continue
        api = ""
        try:
            api = hostapis[int(d["hostapi"])]["name"]
        except Exception:  # pragma: no cover
            pass
        out.append(
            DeviceInfo(
                index=i,
                name=str(d["name"]),
                max_input_channels=int(d["max_input_channels"]),
                default_samplerate=float(d["default_samplerate"]),
                is_default=(i == default_in),
                hostapi=api,
            )
        )
    return out


def resolve_device(spec: str | int | None) -> int | None:
    """Accept an index, an exact name, or a case-insensitive substring."""
    if spec is None or spec == "" or spec == "default":
        return None
    if isinstance(spec, int):
        return spec
    text = str(spec).strip()
    if text.isdigit():
        return int(text)
    devices = list_devices()
    for d in devices:
        if d.name == text:
            return d.index
    lowered = text.casefold()
    for d in devices:
        if lowered in d.name.casefold():
            return d.index
    raise ValueError(f"no input device matching {spec!r}")


def audiotee_candidates() -> list[dict[str, Any]]:
    """Processes worth tapping on macOS (browsers and conferencing apps)."""
    if platform.system() != "Darwin":
        return []
    names = [
        "Google Chrome",
        "Chromium",
        "Brave Browser",
        "Safari",
        "Firefox",
        "Arc",
        "zoom.us",
        "Microsoft Teams",
        "Discord",
        "Slack",
    ]
    out: list[dict[str, Any]] = []
    for name in names:
        try:
            res = subprocess.run(["pgrep", "-x", name], capture_output=True, text=True, timeout=3)
        except Exception:  # pragma: no cover
            continue
        pids = [int(p) for p in res.stdout.split() if p.strip().isdigit()]
        if pids:
            out.append({"name": name, "pids": pids})
    return out


def audiotee_binary() -> str | None:
    from pathlib import Path

    local = Path(__file__).resolve().parents[3] / "helpers" / "audiotee" / "bin" / "audiotee"
    if local.is_file():
        return str(local)
    return shutil.which("audiotee")
