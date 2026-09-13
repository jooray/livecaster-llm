#!/usr/bin/env bash
# Start Livecaster and open the UI in a browser once the server answers.
#
#   ./start.sh                     # newest outline in outlines/, or none at all
#   ./start.sh outlines/ep12.md    # a specific outline
#   ./start.sh --mode remote --set llm.tick_interval_s=15
#
# The first argument is the outline if it is not a flag; everything else is
# passed straight through to `livecaster run`. `--sync` re-installs the
# dependencies first; `--no-open` keeps the browser shut.

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

VENV="${UV_PROJECT_ENVIRONMENT:-.venv}"

usage() {
    sed -n '2,10p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

die() {
    printf '%s\n' "$*" >&2
    exit 1
}

# --- arguments -------------------------------------------------------------

case "${1:-}" in
    -h | --help) usage; exit 0 ;;
esac

sync_first=false
args=()
for arg in "$@"; do
    if [ "$arg" = "--sync" ]; then
        sync_first=true
    else
        args+=("$arg")
    fi
done

# `--open` means the app opens the browser itself; `--no-open` means nobody does.
open_here=true
for arg in ${args[@]+"${args[@]}"}; do
    case "$arg" in
        --open | --no-open) open_here=false ;;
    esac
done

# A resumed session brings its own copy of the outline; do not pick a second one.
resuming=false
for arg in ${args[@]+"${args[@]}"}; do
    case "$arg" in
        --resume | --resume=*) resuming=true ;;
    esac
done

# The outline is the first positional argument, as in `livecaster run`.
outline=""
if [ "${#args[@]}" -gt 0 ] && [ "${args[0]#-}" = "${args[0]}" ]; then
    outline="${args[0]}"
    [ -f "$outline" ] || die "outline not found: $outline"
    if [ "${#args[@]}" -gt 1 ]; then args=("${args[@]:1}"); else args=(); fi
elif [ "$resuming" = false ]; then
    newest=""
    for f in outlines/*.md; do
        [ -e "$f" ] || continue
        [ -n "$newest" ] && [ ! "$f" -nt "$newest" ] && continue
        newest="$f"
    done
    outline="$newest"
    if [ -n "$outline" ]; then
        echo "outline: $outline (the newest in outlines/ — pass one to pick another)"
    else
        echo "no outline in outlines/ — drop a Markdown file on the map once it opens"
    fi
fi

# --- dependencies ----------------------------------------------------------

command -v uv >/dev/null 2>&1 ||
    die "uv is not installed: https://docs.astral.sh/uv/getting-started/installation/"

if [ ! -x "$VENV/bin/livecaster" ] || [ "$sync_first" = true ]; then
    extras=()
    case "$(uname -s)" in
        Darwin) extras+=(--extra mac) ;;
        Linux)
            if command -v nvidia-smi >/dev/null 2>&1; then
                extras+=(--extra cuda)
            else
                extras+=(--extra linux)
            fi
            ;;
    esac
    # Keep the optional engines that are already installed: a plain sync would
    # drop the Whisper fallback out from under a config that asks for it.
    if [ -x "$VENV/bin/python" ]; then
        while read -r module extra; do
            "$VENV/bin/python" -c "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('$module') else 1)" \
                2>/dev/null && extras+=(--extra "$extra")
        done <<'EXTRAS'
mlx_whisper mac-whisper
faster_whisper whisper
anthropic anthropic
EXTRAS
    fi
    echo "syncing dependencies: uv sync ${extras[*]}"
    uv sync ${extras[@]+"${extras[@]}"}
fi

if [ -z "${VENICE_API_KEY:-}" ] && ! grep -qs '^ *VENICE_API_KEY=' .env; then
    echo "warning: VENICE_API_KEY is not set and .env does not define it — ticks will fail" >&2
fi

# --- where the UI will be --------------------------------------------------

# Ask the app's own config loader, so livecaster.toml, LIVECASTER_UI__PORT,
# --config and --set all resolve exactly the way the server resolves them.
config_arg=""
set_args=()
expect=""
for arg in ${args[@]+"${args[@]}"}; do
    case "$expect" in
        config) config_arg="$arg"; expect=""; continue ;;
        set) set_args+=("$arg"); expect=""; continue ;;
    esac
    case "$arg" in
        --config) expect=config ;;
        --config=*) config_arg="${arg#--config=}" ;;
        --set) expect=set ;;
        --set=*) set_args+=("${arg#--set=}") ;;
    esac
done

host=127.0.0.1
port=8766
resolved=$(
    uv run --no-sync python - "$config_arg" ${set_args[@]+"${set_args[@]}"} <<'PY' 2>/dev/null || true
import sys

from dotenv import load_dotenv

load_dotenv(".env", override=False)
from livecaster.config import load_config

cfg = load_config(sys.argv[1] or None, sys.argv[2:])
host = cfg.ui.host
print("127.0.0.1" if host in ("0.0.0.0", "") else host, cfg.ui.port)
PY
)
if [ -n "$resolved" ]; then
    read -r host port <<<"$resolved"
fi

case "$host" in
    *:*) url="http://[$host]:$port/" ;;   # IPv6 literal
    *) url="http://$host:$port/" ;;
esac

listening() {
    (exec 3<>"/dev/tcp/$host/$port") >/dev/null 2>&1
}

if listening; then
    die "port $port is already in use — another Livecaster is probably still running.
Close it, or pick another port with: ./start.sh --set ui.port=$((port + 1))"
fi

# --- browser ---------------------------------------------------------------

open_url() {
    if [ -n "${BROWSER:-}" ]; then
        "$BROWSER" "$1" >/dev/null 2>&1 &
    elif command -v open >/dev/null 2>&1; then
        open "$1" >/dev/null 2>&1
    elif command -v xdg-open >/dev/null 2>&1; then
        xdg-open "$1" >/dev/null 2>&1
    else
        echo "open $1 in your browser"
    fi
}

opener_pid=""
if [ "$open_here" = true ]; then
    args+=(--no-open)
    (
        # The model can take a while to load before uvicorn binds; 60 s of
        # patience, then give up quietly rather than open a dead tab.
        for _ in $(seq 1 240); do
            if listening; then
                open_url "$url"
                exit 0
            fi
            sleep 0.25
        done
    ) &
    opener_pid=$!
    trap 'kill "$opener_pid" 2>/dev/null || true' EXIT
fi

# --- go --------------------------------------------------------------------

echo "UI: $url"
if [ -n "$outline" ]; then
    uv run --no-sync livecaster run "$outline" ${args[@]+"${args[@]}"}
else
    uv run --no-sync livecaster run ${args[@]+"${args[@]}"}
fi
