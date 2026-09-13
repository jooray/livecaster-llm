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

## Demo

[![Livecaster demo](https://img.youtube.com/vi/rDJfGJWyTkM/hqdefault.jpg)](https://youtu.be/rDJfGJWyTkM)

A walkthrough recorded with Livecaster running on itself: <https://youtu.be/rDJfGJWyTkM>
(Slovak; an English version is planned). The outline it follows is
[`demo/demo-sk.md`](demo/demo-sk.md), with an English translation in
[`demo/demo.md`](demo/demo.md).

See [`SPEC.md`](SPEC.md) for the full specification, [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md)
for the milestone plan, and [`llms.txt`](llms.txt) if you are an agent setting this up.

## Install

Requires Python 3.12 (pinned) and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/jooray/livecaster-llm && cd livecaster-llm

# macOS (Apple Silicon): Parakeet via MLX
uv sync --extra mac

# Linux: Parakeet via onnxruntime
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

`check` verifies the Venice key and models, resolves every microphone named in `livecaster.toml`,
downloads and self-tests the STT model, lists audio devices, and (on macOS) reports AudioTee
availability and the permissions you need to grant. A device that was renamed or is simply not
plugged in is reported here rather than at the moment you press Start — though it no longer stops a
recording: an unavailable microphone falls back to whichever one is connected, and says so.
The first run downloads about 2.4 GB of Parakeet weights, so do not leave it until five minutes
before a recording; `--no-stt` skips that part. On a flaky connection, pull the model separately
with `uv run hf download mlx-community/parakeet-tdt-0.6b-v3`, which retries properly instead of
restarting.

## First run

Write an outline, or start from one of the demo outlines:

```bash
mkdir -p outlines && cp demo/demo.md outlines/my-episode.md
uv run livecaster run outlines/my-episode.md --mode live
```

`outlines/` is git-ignored, because an episode plan usually has guest notes and private context in
it. Any Markdown works: headings, bullets, nested bullets, `Question:` lines, whole paragraphs.

**Bold a line and it becomes a must-ask.** `**Closing question:** what did we never get to?` is the
one you do not want to reach the end without asking, and the show notes say so if you did. A whole
bold line counts too. A bold run used to title a bullet — `- **Psychedelics vs. breath** — how do
they compare?` — deliberately does not, or every titled bullet would be urgent.

You can also start with nothing — `uv run livecaster run` — and drop the Markdown file onto the map
once the UI is open, or pick it under the cog. The file is copied into the session directory and
watched from there, so you can keep editing it while you record.

This parses the outline, starts the server on <http://127.0.0.1:8766> and opens the UI. Unless you
pass `--no-preflight`, one LLM pass then runs in the background and fills in suggested questions and
trigger phrases per topic; the map is on screen while that happens. Press **Start** in the UI when
you are ready and **Finish** when you are done.

`./start.sh` is the same thing without the typing: it installs the dependencies on a first run,
takes the newest outline in `outlines/` when you do not name one, and opens the browser once the
server actually answers rather than a second after launch.

```bash
./start.sh                                   # newest outline in outlines/
./start.sh outlines/my-episode.md --mode remote
./start.sh --sync                            # re-install dependencies first
```

Anything it does not recognise goes straight to `livecaster run`.

The clock starts at **Start**, not when the process launched, so chapter and subtitle timestamps
match the recording rather than however long you spent setting up.

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
    --outline demo/demo.md --mock-llm --speed 0

# Replay at 4x with the UI open
uv run livecaster replay --transcript tests/fixtures/transcript_sk.jsonl \
    --outline demo/demo.md --mock-llm --speed 4 --serve

# Replay a WAV file through real STT
uv run livecaster replay --wav your-sample.wav --outline demo/demo.md --mock-llm
```

## Remote mode (Zencastr, Meet, Riverside …)

Remote mode captures two channels: your microphone (Host) and the browser's audio output (Guest).
**Wear headphones.** Otherwise the guest's voice re-enters your microphone and only the cross-talk
dedupe saves you.

### macOS, option A: AudioTee (per-process tap, macOS 14.2+)

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

### macOS, option B: BlackHole (virtual loopback)

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

### Linux: PipeWire/PulseAudio monitor

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
| `livecaster run [outline.md] [--mode live\|remote] [--resume DIR] [--slug S] [--no-preflight] [--lan] [--set k=v]` | Start a session. Without an outline, drop one on the map. |
| `livecaster devices` | List input devices (and AudioTee candidate processes on macOS). |
| `livecaster replay <file\|dir> [--outline M] [--speed N] [--mock-llm] [--serve]` | Run the pipeline from a file. The source can also be given as `--transcript`, `--wav` or `--session`. |
| `livecaster wrapup <session_dir> [--model M] [--resolve-links]` | Re-run the wrap-up on an existing session. |
| `livecaster export <session_dir>` | Re-render Markdown/SRT from existing JSON, no LLM calls. |
| `livecaster check` | Venice key and models, STT self-test, devices, AudioTee, permissions. |
| `livecaster --version` | Print the version. |

Every configuration key can be overridden per run: `--set llm.tick_interval_s=15`.

## If it crashes

Everything is on disk as it happens, so a crash costs at most the utterance in flight:

| File | Written |
|---|---|
| `session.json` | ≤ 1 s after any change, and at least every 30 s: node states, evidence, mentions, usage |
| `transcript.jsonl` | as each utterance is transcribed |
| `events.jsonl` | on every start/pause/finish/mark/sync |
| `audio/*.wav` | continuously while capturing |
| `llm.jsonl` | every LLM request and response |

```bash
uv run livecaster run outlines/osnova.md --resume sessions/2026-09-03_osnova
```

Resume reloads the transcript, restores every covered/touched/skipped mark and the clock (from the
furthest of the transcript end, the recorded duration and the last sync mark, because a long silence
before the crash still happened), and picks up where it stopped. Pressing **Record again** after a
`Finish` does the same thing without leaving the app; the wrap-up re-runs over everything at the
end. `livecaster wrapup <dir>` and `livecaster export <dir>` regenerate the notes from a session
directory alone, with or without the LLM.

## Configuration

Livecaster reads `livecaster.toml` from the working directory, then `~/.config/livecaster/`.
Copy [`livecaster.toml.example`](livecaster.toml.example) and edit. Environment variables
(`LIVECASTER_LLM__TICK_MODEL=...`) and `--set` overrides win over the file, in that order.

### From the UI

The cog in the top bar (or `,`) opens Settings, which covers the three things that change between
episodes without a restart:

- **Audio channels.** Every input device the machine has, plus the AudioTee candidates on macOS.
  Add or drop a channel, rename one, mark which one is `direct`. Applied while recording, the
  microphones are reopened in place and the clock, the transcript and the map carry on.
- **Models.** The tick model, the wrap-up model and the speech engine. The LLM half lands on the
  next tick; a new speech engine loads at the next Start, because the worker thread owns a loaded
  model. The suggestion list is the provider's own, fetched when the dialog opens.
- **Episode.** A target length in minutes. Leave it empty and the app just shows the clock; set it
  and the map can weigh what is still unasked against the time left. `session.target_minutes = 60`
  in the file makes it the default.
- **Outline.** Load a different Markdown file. Dropping one anywhere on the map does the same.

None of it is written back to `livecaster.toml` — the file keeps your comments, and the dialog is
for the evening's exception. Put anything you want every time in the file.

The default source is `device:auto`, which is whichever microphone is plugged in at Start, the
system default first. Naming a device pins it; if that name is gone when you press Start — the
headset is in its case — Livecaster records from an available one and says so in a toast, rather
than refusing to start.

## Keyboard

`A`…`Z` jump to the system with that letter in the margin · `1`/`2`/`3` go to a suggestion by rank ·
`j`/`k` move the selection · `c` covered · `x` skipped · `p` pin · `t` transcript · `q` questions ·
`l` links promised · `n` new topics · `w` the notes · `m` sync mark · `d` compact/full ·
`space` pause/resume · `,` settings · `?` help.

The outline is drawn as a score. Each section is a *system* with a lettered rehearsal mark in the
margin, and that letter is how you jump there. An orchestra re-enters at any bar for the same reason
you need to when a guest wanders. Covered lines take a single engraver's cut with the time beside
them; a section you have finished rests to one line, in place, so the plan visibly shrinks as you
work. Click it to open it back up.

The one topic that just became reachable is set as the passage to play now, in pencil red, with the
model's reason and a sentence you could use to get there. A line you **bolded** in the outline is a
must-ask and carries a marcato accent until you have answered it.

Nothing else sits on the screen. The transcript, the questions, the links you promised and the topics
that were not in your plan are keys that overlay and leave again. None of it is meant to be read in
full while you are talking.

## Privacy and cost

Only the outline text, transcript text and derived state go to Venice. Audio and the WAV backups
stay on the machine. Venice's injected system prompt is disabled and web search is off (except the
optional link-resolution step in the wrap-up).

A two-hour episode costs about **$0.50**. Measured on real sessions: a tick averages **$0.0009**
(most of every prompt comes back from Venice's cache), so up to 290 of them come to ~$0.27; the
Sonnet 5 wrap-up measured **$0.14 to 0.20** on short episodes and is dominated by the ~9 to 12k tokens it
writes, not by the transcript it reads. A whole six-minute live session cost **$0.17**.
Livecaster ticks with
`deepseek-v4-flash-0731-fast`, which answers in ~9 s instead of the plain model's ~38 s, and wraps
up on `claude-sonnet-5`, where latency does not matter and the writing does. `DECISIONS.md` (D8,
D17) has the measurements. To trade quality back for cost:

```bash
uv run livecaster run outlines/osnova.md --set llm.tick_model=deepseek-v4-flash-0731 \
    --set llm.final_model=deepseek-v4-flash-0731
```

## Models and providers

A model is named `provider:model`, or bare to use `llm.default_provider` (Venice). Venice proxies
the Claude and GPT families itself, so the default configuration bills everything to one set of
prepaid Venice credits:

| Setting | Default | Notes |
|---|---|---|
| `llm.tick_model` | `deepseek-v4-flash-0731-fast` | 3 to 8 s per tick, ~$0.0009 each |
| `llm.final_model` | `claude-sonnet-5` | Sonnet 5 through Venice; $0.14 to 0.20 per episode |

To bill Anthropic or OpenAI directly instead, prefix the model and supply that provider's key:

```bash
uv sync --extra anthropic         # only for the direct Anthropic path
export ANTHROPIC_API_KEY=...
uv run livecaster run outlines/osnova.md --set llm.final_model=anthropic:claude-sonnet-5
```

`livecaster check` confirms every configured model exists at the provider it routes to, and prints
that provider's prices. Add or re-point providers under `[llm.providers.*]` in `livecaster.toml`
(see [`livecaster.toml.example`](livecaster.toml.example)).

## Language

Parakeet v3 detects the language per utterance and **cannot be told which one to use**. Its
vocabulary has `<|sk|>` and friends, but neither `parakeet-mlx` nor priming the decoder reaches
them (`DECISIONS.md`, D20). On poor audio it drifts: a Slovak recording through a Bluetooth headset
mic came back partly in Polish and once in Russian.

Three things you can do about it, in increasing order of cost:

1. **Lock the language in the UI.** The 🌐 pill in the top bar, changeable mid-session. With
   Parakeet this fixes the language of the notes and prompts and drops any line that comes back in
   an alphabet Slovak never uses. It does not fix Polish-looking Slovak.
2. **Use a better microphone.** Most of the drift is the 16 kHz Bluetooth headset link, not the
   model. `livecaster devices` flags devices running in headset mode.
3. **Switch to Whisper, which honours the language.** Measured on the same 23 utterances, every
   line stayed Slovak and the words improved ("Ako to celé urobiť?" for "Ako to celę robić?").
   Whisper pads every input to a 30 s window, so it costs the same for "tak" as for a sentence:

   | Engine | RTF | Per utterance | Needs |
   |---|---|---|---|
   | `parakeet-mlx` (default) | 0.055 | ~0.25 s | nothing, but cannot be forced |
   | `whisper-mlx` | 0.62 | **2.6 s** | `uv sync --extra mac-whisper` (pulls PyTorch) |
   | `faster-whisper` | 3.68 | 15 s | `uv sync --extra whisper` (no PyTorch) |

   ```bash
   # Live language lock on Apple Silicon. The transcript lands ~2.5 s later, which the
   # 25 s tick loop does not notice.
   uv sync --extra mac-whisper
   uv run livecaster run outlines/osnova.md --set stt.engine=whisper-mlx --set stt.language=sk

   # No PyTorch, but 15 s per utterance: right for re-running a recording, not for live.
   uv sync --extra whisper
   uv run livecaster replay sessions/<dir> --set stt.engine=faster-whisper --set stt.language=sk
   ```

   `faster-whisper` does not get faster with more threads (4.63 / 4.66 / 4.67 RTF at 4 / 8 / 12).

## Known issues

- Parakeet v3 cannot be forced to a language. See [Language](#language) above.
- `reasoning_effort = "low"` on DeepSeek V4 Flash spends the whole token budget on reasoning and
  returns nothing. Livecaster sends `"none"`; do not raise it without re-measuring.
- Single-microphone mode has no speaker labels by design; the prompts and exports handle their absence.
- macOS system-audio permission is granted to the *terminal app*, not to Livecaster, and some
  terminals never raise the prompt. BlackHole is the documented fallback.
- Opening a Bluetooth headset's microphone puts the whole link into 16 kHz mono handsfree mode, so
  the headphones themselves start to sound like a phone call. That is macOS, not Livecaster. Use a
  wired or USB microphone for a real episode.

## Documentation

- [`docs/RUNBOOK.md`](docs/RUNBOOK.md): pre-show checklist, during-show keys, post-show steps.
- [`DECISIONS.md`](DECISIONS.md): decisions taken during implementation.
