"""Input device enumeration (FR-06)."""

from __future__ import annotations

import platform
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any

from livecaster.log import get_logger

log = get_logger(__name__)

#: Source specs that mean "whatever microphone this machine has right now". A
#: headset that is in its case should not be a reason to refuse to record.
AUTO_DEVICE = {"auto", "first", "first-available"}


@dataclass
class DeviceInfo:
    index: int
    name: str
    max_input_channels: int
    default_samplerate: float
    is_default: bool = False
    hostapi: str = ""
    max_output_channels: int = 0
    #: True when a device of this name also exists as an output. CoreAudio splits
    #: a Bluetooth headset into two entries — "WH-1000XM6" in 1 out 0 at 16 kHz,
    #: and "WH-1000XM6" in 0 out 2 at 44.1 kHz — so this is how the pair is
    #: recognised; the input side alone looks like any other narrow-band mic.
    has_output_twin: bool = False

    @property
    def is_headset_mode(self) -> bool:
        """A Bluetooth headset whose microphone is open, so macOS is in HFP.

        In that mode the *playback* side collapses to the same narrow 16 kHz mono
        link — which is why the headphones suddenly sound like a phone call. It
        costs nothing for speech recognition and everything for what you hear.
        """
        both_ways = self.max_output_channels > 0 or self.has_output_twin
        return self.max_input_channels > 0 and both_ways and self.default_samplerate <= 24_000


def list_devices() -> list[DeviceInfo]:
    import sounddevice as sd

    default_in = None
    try:
        default_in = sd.default.device[0]
    except Exception:  # pragma: no cover
        pass
    hostapis = sd.query_hostapis()
    devices = list(sd.query_devices())
    outputs = {str(d["name"]) for d in devices if int(d["max_output_channels"]) > 0}
    out: list[DeviceInfo] = []
    for i, d in enumerate(devices):
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
                max_output_channels=int(d["max_output_channels"]),
                has_output_twin=str(d["name"]) in outputs,
            )
        )
    return out


def default_input_device() -> DeviceInfo | None:
    """The system default input, or simply the first one this machine has."""
    devices = list_devices()
    if not devices:
        return None
    for d in devices:
        if d.is_default:
            return d
    return devices[0]


def resolve_device(spec: str | int | None) -> int | None:
    """Accept an index, an exact name, a case-insensitive substring, or ``auto``.

    ``auto`` is whichever microphone exists right now, the system default first.
    It is the default source precisely so that a config file never has to name a
    Bluetooth headset that is only sometimes connected.
    """
    if spec is None or spec == "" or spec == "default":
        return None
    if isinstance(spec, int):
        return spec
    text = str(spec).strip()
    if text.isdigit():
        return int(text)
    if text.casefold() in AUTO_DEVICE:
        chosen = default_input_device()
        if chosen is None:
            raise ValueError("this machine has no audio input device")
        return chosen.index
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
