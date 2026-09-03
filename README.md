# Livecaster

A live co-pilot for podcast hosts. While you record, Livecaster transcribes the conversation
**locally** on your machine and every ~25 seconds asks a fast LLM to reason over your Markdown
outline against the recent transcript. The UI shows your outline as a **live map**, not a script:
covered topics get struck through, topics that just became relevant light up with a reason and a
suggested segue, and follow-ups plus "things we need to link" collect in a side panel.

When the session ends it writes show notes in the language of the podcast: what was covered and
what was not, chapters with timestamps, title and description candidates, and mentions with links
or explicit link TODOs.

**Your outline file is never modified. Audio never leaves the machine.**

See [`SPEC.md`](SPEC.md) for the full specification and [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md)
for the milestone plan.

## Install

Requires Python 3.12 (pinned) and [uv](https://docs.astral.sh/uv/).

```bash
git clone <this repo> && cd livecaster-llm

# macOS (Apple Silicon) — Parakeet via MLX
uv sync --extra mac

# Linux — Parakeet via onnxruntime
uv sync --extra linux
# ...or with an NVIDIA GPU
uv sync --extra cuda
```

Set your Venice API key (a `.env` file in the project root works too):

```bash
export VENICE_API_KEY=...
```

Then check the install:

```bash
uv run livecaster check
```

`check` verifies the Venice key and models, downloads and self-tests the STT model, lists audio
devices, and (on macOS) reports AudioTee availability and the permissions you need to grant.
The first run downloads about 2.4 GB of Parakeet weights, so do not leave it until five minutes
before a recording; `--no-stt` skips that part. On a flaky connection, pull the model separately
with `uv run hf download mlx-community/parakeet-tdt-0.6b-v3`, which retries properly instead of
restarting.

## First run

```bash
uv run livecaster run osnova.md --mode live
```

This parses the outline, starts the server on <http://127.0.0.1:8765>, opens the UI, and (unless
`--no-preflight`) runs one LLM pass that suggests questions and trigger phrases per topic.
Press **Start** in the UI when you are ready; press **Finish** when you are done.

Outputs land in `sessions/<date>_<slug>/final/`:
`show_notes.md`, `outline_annotated.md`, `transcript.md`, `transcript.srt`, `final_analysis.json`.

The Whisper fallback is a separate install on macOS, because `mlx-whisper` pulls in PyTorch:

```bash
uv sync --extra mac --extra mac-whisper   # only if you need a language Parakeet does not cover
```

## Developing without a microphone

```bash
# Replay a transcript through the whole pipeline with canned LLM answers
uv run livecaster replay --transcript tests/fixtures/transcript_sk.jsonl \
    --outline osnova.md --mock-llm --speed 0

# Replay at 4x with the UI open
uv run livecaster replay --transcript tests/fixtures/transcript_sk.jsonl \
    --outline osnova.md --mock-llm --speed 4 --serve

# Replay a WAV file through real STT
uv run livecaster replay --wav tests/fixtures/speech_sk_30s.wav --outline osnova.md --mock-llm
```

## Remote mode (Zencastr, Meet, Riverside …)

Remote mode captures two channels: your microphone (Host) and the browser's audio output (Guest).
**Wear headphones** — otherwise the guest's voice re-enters your microphone and only the cross-talk
dedupe saves you.

### macOS, option A — AudioTee (per-process tap, macOS 14.2+)

```bash
./helpers/audiotee/build.sh          # clones and builds the helper with Swift
```

Then in `livecaster.toml`:

```toml
[[audio.channels]]
name = "Guest"
source = "audiotee:Google Chrome"
is_direct = true
```

The first run triggers the macOS *system audio recording* permission prompt for your **terminal
app** (System Settings → Privacy & Security → Screen & System Audio Recording). Some terminals never
raise the prompt; if yours does not, use option B.

### macOS, option B — BlackHole (virtual loopback)

1. `brew install blackhole-2ch`
2. In *Audio MIDI Setup*, create a **Multi-Output Device** containing your headphones **and**
   BlackHole 2ch, and select it as the system output so you still hear the guest.
3. Point the Guest channel at BlackHole:

```toml
[[audio.channels]]
name = "Guest"
source = "device:BlackHole 2ch"
is_direct = true
```

### Linux — PipeWire/PulseAudio monitor

```bash
pactl list sources short | grep monitor
```

```toml
[[audio.channels]]
name = "Guest"
source = "device:alsa_output.pci-0000_00_1f.3.analog-stereo.monitor"
is_direct = true
```

### Sync mark

Zencastr starts recording at a different moment than Livecaster. Press **`m`** (or the *Sync mark*
button) the instant the external recorder starts; chapter timestamps in the show notes are then
relative to that mark.

## CLI reference

| Command | What it does |
|---|---|
| `livecaster run <outline.md> [--mode live\|remote] [--resume DIR] [--slug S] [--no-preflight] [--set k=v]` | Start a session. |
| `livecaster devices` | List input devices (and AudioTee candidate processes on macOS). |
| `livecaster replay <file\|dir> [--outline M] [--speed N] [--mock-llm] [--serve]` | Run the pipeline from a file. The source can also be given as `--transcript`, `--wav` or `--session`. |
| `livecaster wrapup <session_dir> [--model M] [--resolve-links]` | Re-run the wrap-up on an existing session. |
| `livecaster export <session_dir>` | Re-render Markdown/SRT from existing JSON, no LLM calls. |
| `livecaster check` | Venice key and models, STT self-test, devices, AudioTee, permissions. |
| `livecaster --version` | Print the version. |

Every configuration key can be overridden per run: `--set llm.tick_interval_s=15`.

## Configuration

Livecaster reads `livecaster.toml` from the working directory, then `~/.config/livecaster/`.
Copy [`livecaster.toml.example`](livecaster.toml.example) and edit. Environment variables
(`LIVECASTER_LLM__TICK_MODEL=...`) and `--set` overrides win over the file, in that order.

## Keyboard

`j`/`k` move the selection · `c` covered · `x` skipped · `p` pin · `1`/`2`/`3` jump to a hot item ·
`m` sync mark · `t` toggle transcript · `space` pause/resume · `?` help.

## Privacy and cost

Only the outline text, transcript text and derived state go to Venice. Audio and the WAV backups
stay on the machine. Venice's injected system prompt is disabled and web search is off (except the
optional link-resolution step in the wrap-up).

A two-hour episode costs about **$0.30**: roughly 290 ticks at ~$0.001 each (96 % of every prompt
comes back from Venice's cache) plus a cent for the wrap-up. Livecaster ticks with
`deepseek-v4-flash-0731-fast`, which answers in ~9 s instead of the plain model's ~38 s; the
wrap-up uses the cheaper plain model, where latency does not matter. `DECISIONS.md` (D8) has the
measurements. To trade latency back for cost:

```bash
uv run livecaster run osnova.md --set llm.tick_model=deepseek-v4-flash-0731
```

## Known issues

- Parakeet v3 auto-detects its language and cannot be forced; if you need a forced language or one
  outside its 25, configure `stt.engine = "faster-whisper"` (Linux) or, on macOS,
  `uv sync --extra mac-whisper` first — `mlx-whisper` pulls in PyTorch, so it is not part of the
  default macOS install (`DECISIONS.md`, D7).
- `reasoning_effort = "low"` on DeepSeek V4 Flash spends the whole token budget on reasoning and
  returns nothing. Livecaster sends `"none"`; do not raise it without re-measuring.
- Single-microphone mode has no speaker labels by design; the prompts and exports handle their absence.
- macOS system-audio permission is granted to the *terminal app*, not to Livecaster, and some
  terminals never raise the prompt. BlackHole is the documented fallback.

## Documentation

- [`docs/RUNBOOK.md`](docs/RUNBOOK.md) — pre-show checklist, during-show keys, post-show steps.
- [`DECISIONS.md`](DECISIONS.md) — decisions taken during implementation.
