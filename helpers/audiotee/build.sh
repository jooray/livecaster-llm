#!/usr/bin/env bash
# Build the AudioTee helper (per-process system-audio capture, macOS 14.2+).
#
#   ./helpers/audiotee/build.sh
#
# Leaves the binary at helpers/audiotee/bin/audiotee, which livecaster finds
# automatically. On any other platform, or if you would rather not install a
# Swift toolchain, use BlackHole instead — see README.md.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$HERE/src"
BIN="$HERE/bin"
REPO="https://github.com/makeusabrew/audiotee.git"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "AudioTee is macOS only. On Linux use a PipeWire/PulseAudio monitor source." >&2
  exit 1
fi

macos_major="$(sw_vers -productVersion | cut -d. -f1)"
macos_minor="$(sw_vers -productVersion | cut -d. -f2)"
if (( macos_major < 14 )) || { (( macos_major == 14 )) && (( macos_minor < 2 )); }; then
  echo "AudioTee needs macOS 14.2 or newer (found $(sw_vers -productVersion))." >&2
  echo "Use BlackHole instead — see README.md." >&2
  exit 1
fi

if ! command -v swift >/dev/null 2>&1; then
  echo "swift not found. Install Xcode or the Command Line Tools:" >&2
  echo "  xcode-select --install" >&2
  exit 1
fi

if [[ -d "$SRC/.git" ]]; then
  echo "==> updating $SRC"
  git -C "$SRC" pull --ff-only
else
  echo "==> cloning $REPO"
  rm -rf "$SRC"
  git clone --depth 1 "$REPO" "$SRC"
fi

echo "==> building (release)"
( cd "$SRC" && swift build -c release )

mkdir -p "$BIN"
cp "$SRC/.build/release/audiotee" "$BIN/audiotee"
chmod +x "$BIN/audiotee"

echo
echo "built: $BIN/audiotee"
echo
echo "Next: the first capture asks your TERMINAL app for permission."
echo "  System Settings -> Privacy & Security -> Screen & System Audio Recording"
echo "If your terminal never raises the prompt, use BlackHole (see README.md)."
echo
echo "Check it with:  uv run livecaster devices"
