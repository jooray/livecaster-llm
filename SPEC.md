# Livecaster — Specification

Version 0.1 · 2026-09-03 · Status: draft for implementation
Companion document: `IMPLEMENTATION_PLAN.md` (milestones, tasks, acceptance tests).

## 0. Summary

Livecaster is a local desktop tool for a podcast host. While the host records a conversation (in person, or remotely through Zencastr), Livecaster listens to the audio, transcribes it locally on the machine, and every ~25 seconds asks a fast LLM (DeepSeek V4 Flash 0731 via Venice) to reason over the host's Markdown outline against the recent transcript. The UI shows the outline as a **live map**, not a script:

- topics already discussed are struck through,
- topics that just became relevant (because the guest said something related, anywhere in the outline, in any order) light up with a one-line reason and a suggested segue,
- follow-up questions and "things we mentioned that need a link" accumulate in a side panel.

When the session ends, Livecaster writes show notes in the language of the podcast: what was covered and what was not, chapter markers with timestamps, title and description candidates, and a list of mentions with links (or explicit link TODOs).

The source outline file is never modified.

## 1. Goals and non-goals

### Goals

1. Near-real-time local transcription of a two-person conversation on Apple Silicon (primary) and Linux (secondary). English, Slovak and Czech are first-class languages; the other languages of the default engine work too.
2. A live, non-linear map of the outline: covered / touched / hot / skipped states per topic, with reasons.
3. Suggestions, not instructions: the host decides where to go; the app never auto-scrolls or advances.
4. Wrap-up show notes in the podcast language, produced from both the transcript and the outline.
5. Cheap and robust: a two-hour episode should cost well under one US dollar in LLM calls, and LLM or network failures must never interrupt transcription or lose data.

### Non-goals (v1)

- Not a teleprompter. No sequential "next line" view, no autoscroll.
- Not a recorder of record. Zencastr (remote) or the host's usual recorder (in person) remains the source of the published audio. Livecaster keeps a backup WAV only.
- Does not edit the outline `.md`. All annotations live in the session directory.
- No cloud speech recognition. Only transcript text is sent to Venice.
- No speaker diarization from a single shared microphone. Speaker labels come from separate audio channels (one per person) when available.
- No accounts, no multi-user, no remote access beyond the local network.

## 2. Scenarios

### S1 — In person

Host and guest sit in one room. Audio comes from one microphone (both voices, no speaker distinction) or from an audio interface with one microphone per person (two channels, speaker known). The single-microphone case is a first-class mode: the whole pipeline, the prompts and the exports work without speaker labels. The host's real recording runs on their usual recorder. Livecaster runs on the host's laptop; the UI is open on the laptop or on a tablet on the local network.

### S2 — Remote via Zencastr

Host talks into a microphone; the guest's voice arrives through the browser's audio output. Zencastr records each side locally. Livecaster captures two channels: the microphone (Host) and the browser's audio output (Guest). On macOS the guest channel is either a loopback virtual device (BlackHole) or a per-process tap of the browser via the AudioTee helper (macOS 14.2+). On Linux it is the PipeWire/PulseAudio monitor source. Because Zencastr's recording starts at a different moment than the Livecaster session, the host presses a **sync mark** key when Zencastr starts recording so chapter timestamps in the show notes line up with the published audio.

### S3 — Replay / development

Livecaster runs from a WAV file or from a previously saved transcript, at real time or faster, with a real or mocked LLM. This is how the pipeline is developed and tested without speaking into a microphone.

### A session, end to end

1. Host writes `osnova.md` (see `osnova.md` in this repository for a real example: Slovak, mixed headings, nested bullets, bold question lines, long prose paragraphs).
2. `livecaster run osnova.md --mode remote`. The app parses the outline, assigns IDs, starts the web server, opens the UI, and runs an optional **pre-flight** LLM pass that suggests 2–3 questions per topic, trigger phrases, and cross-links between topics.
3. Host checks input levels, presses **Start**. Transcription begins; the transcript panel fills; every ~25 s a tick updates the map.
4. Guest mentions psychedelics while the conversation is in the physiology section. The item "Psychedeliká vs. dych" under section 3 lights up with "Hosť práve spomenul ayahuascu — sekcia 3 sa na to pýta priamo" and a one-sentence segue. The host may jump there or ignore it.
5. Items get struck through as they are covered. The host can override any state with a click or a key.
6. Host presses **Finish**. Livecaster runs the wrap-up analysis and writes `show_notes.md`, `outline_annotated.md`, `transcript.md`, `transcript.srt`, and `session.json` into the session directory.

## 3. Platform and runtime

| Item | Decision |
|---|---|
| Primary platform | macOS 14+ on Apple Silicon (M1 or later, 16 GB RAM recommended) |
| Secondary platform | Linux x86_64 (CPU works; NVIDIA GPU optional) |
| Language | Python 3.12 (pin in `.python-version`; MLX and onnxruntime wheels lag newer Pythons), managed by `uv`. Never install outside the virtual environment. |
| UI | Browser-based, served from the Python process on `127.0.0.1:8765` (configurable to `0.0.0.0` for a tablet). Plain HTML/CSS/JS, no build step, no service worker. |
| LLM | Venice API (OpenAI-compatible), `https://api.venice.ai/api/v1`, key in `VENICE_API_KEY`. Tick model `deepseek-v4-flash-0731`. Final model configurable, default the same; `deepseek-v4-pro-0813` recommended for wrap-up. |
| STT | Local only. Default engines: `parakeet-mlx` on macOS, `onnx-asr` on Linux, Whisper variants as fallback. See §7.3. |
| Voice activity detection | Silero VAD via `pysilero-vad` (onnxruntime, no torch). |
| Audio I/O | `sounddevice` (PortAudio) for devices; AudioTee subprocess for per-process capture on macOS. |
| Persistence | Plain files in a session directory: JSONL for append-only streams, JSON for snapshots, Markdown for outputs. No database. |

## 4. Functional requirements

Each requirement has an ID for traceability in the implementation plan.

### Outline

- **FR-01** Load a Markdown outline from a path. Parse into a tree of nodes (headings, list items with nesting, paragraphs, question lines, metadata lines). See §7.1.
- **FR-02** Assign every node a short stable ID (`T1`, `T2`, … in document order). Leaves (list items, paragraphs, question lines, headings without children) are **coverable**; headings with children derive their state from their leaves.
- **FR-03** Never write to the source outline file.
- **FR-04** Watch the outline file; on change, re-parse, re-map IDs by normalized text so existing states survive, and push the update to the UI.
- **FR-05** Optional pre-flight pass (one LLM call before the session): per coverable node, 2–3 suggested questions, 3–6 trigger phrases (in the podcast language plus English variants), and related node IDs. Stored in the session, shown on demand in the UI, used by the fast lane (FR-16).

### Audio

- **FR-06** Enumerate input devices (`livecaster devices`). Configure 1..N channels, each with a name (e.g. Host, Guest, Room) and a source: a device (with optional channel index), an AudioTee process tap (macOS), or a file (replay).
- **FR-07** Capture each channel continuously; resample to 16 kHz mono float32 for VAD and STT.
- **FR-08** Show a level meter per channel in the UI, updated at ≥ 5 Hz.
- **FR-09** Optionally record each channel to a WAV file in the session directory (default on). This is a backup, not the published recording.
- **FR-10** Sync mark: a key/button records the current session time as the offset of the external recording. Multiple marks allowed; the last one wins; all are logged.

### Transcription

- **FR-11** Segment each channel into utterances with VAD: end after ≥ 600 ms of silence, minimum 300 ms of speech, hard cut at 20 s. Transcribe each utterance with the configured engine.
- **FR-12** Each transcript segment carries: id, channel, speaker label (absent in single-microphone mode), start and end session time, text, language (if the engine reports it), word timestamps (if available), engine name.
- **FR-13** Segments are appended to `transcript.jsonl` immediately and pushed to the UI. A segment must appear in the UI within 2 s of the end of the utterance on an M1 or better.
- **FR-14** In multi-channel mode, suppress cross-talk duplicates: if two segments from different channels overlap in time and their normalized texts are ≥ 80 % similar, keep the one from the channel whose source is not the microphone (the direct source) and drop the other.
- **FR-15** Language handling: `language = auto | <ISO 639-1>`. On `auto`, the engine detects per utterance if it can; the podcast language used for LLM output is the majority vote over tick reports (§9.2) or the configured value. English, Slovak and Czech must each be verified end to end (transcript, tick output, show notes); a Slovak conversation with English technical terms must stay Slovak in the outputs.

### Live reasoning

- **FR-16** Fast lane (no LLM): on every new segment, match the pre-flight trigger phrases (and outline words, fuzzy) against the segment text and give matching nodes a faint "warm" highlight immediately. This is a hint only; it never changes a node's status.
- **FR-17** Tick loop: every `tick_interval_s` (default 25) run one LLM call if at least `min_new_words` (default 25) new transcript words arrived since the last tick, or run immediately when more than 120 new words arrived. Ticks never overlap; if one is in flight the trigger is skipped. On failure, back off exponentially (5 s → 120 s) and show the state in the UI. Transcription continues regardless.
- **FR-18** Each tick sends: instructions, the full outline with IDs (stable prefix for caching), the current node states, the mentions so far, the last `transcript_window_words` (default 1200) with speaker labels and timestamps, with the new part clearly marked. It receives strict JSON (§9.2) containing: covered nodes with confidence and evidence, touched nodes, hot nodes with score, reason and segue, suggested questions, mentions, new topics not in the outline, current topic summary, and the detected language.
- **FR-19** The app, not the LLM, owns state. A deterministic reducer applies the tick result with thresholds (cover ≥ 0.7, touch ≥ 0.4), decays hot scores that were not re-confirmed, dedupes mentions and questions, and respects manual locks. See §9.3.
- **FR-20** Manual overrides in the UI: mark covered / uncovered, skip / unskip, pin as next / unpin. An uncover action locks the node against LLM re-covering for `manual_lock_minutes` (default 10). A pin places the node in the hot list with score 1.0 until covered or unpinned.
- **FR-21** Every tick's request, response, latency, token usage (including cached tokens) and estimated cost are appended to `llm.jsonl` and summarized in the UI.

### UI

- **FR-22** Outline map: whole outline visible with sections; leaf states rendered as: untouched (normal), warm (faint tint, fast lane), touched (dotted underline + "◐"), hot (colored left bar, rank keycap 1–3, reason and segue under the item), covered (strikethrough, muted, time badge), skipped (muted, "⏭"), pinned (pin icon). Headings show a coverage fraction like `3/7`. No autoscroll ever; when hot items are outside the viewport, edge indicators say "2 hot ▲" / "1 hot ▼".
- **FR-23** Side panel with: Now (current topic one-liner, top 3 suggestions with reasons), Questions (max 5), Mentions (with "needs link" flags and kind icons), Transcript (live, auto-scrolling, speaker colors), New topics (not in outline).
- **FR-24** Top bar: session clock, recording indicator, level meters, STT status (engine, queue depth), LLM status (last tick age, latency, tokens, cost so far), buttons Start / Pause / Resume / Finish, Sync mark, Tick now, Reload outline.
- **FR-25** Keyboard: `j`/`k` move selection, `c` toggle covered, `x` toggle skipped, `p` toggle pin, `1`/`2`/`3` select hot item by rank, `m` sync mark, `t` toggle transcript panel, `space` pause/resume, `?` help.
- **FR-26** Readable from 1–2 m: minimum body font 18 px, high contrast, dark theme by default with a light theme toggle. Works at tablet widths (≥ 768 px) with the side panel collapsing into tabs.
- **FR-27** Version check with automatic reload: the server embeds a build ID (hash of the UI files) in `index.html` and sends it in the WebSocket `hello`. If they differ, the client reloads. Static files are served with `Cache-Control: no-store`. This is not a PWA and must not register a service worker.

### Session and wrap-up

- **FR-28** A session directory `sessions/<YYYY-MM-DD>_<slug>/` holds everything (§8). State is snapshotted to `state.json` within 1 s of any change; the app can resume from a session directory after a crash (`--resume`).
- **FR-29** Finish runs the wrap-up (§9.4): one or two LLM calls over the whole transcript with the final model; then renders `show_notes.md`, `outline_annotated.md`, `transcript.md`, `transcript.srt`, `session.json`.
- **FR-30** Show notes are in the podcast language and contain: summary, chapters with timestamps (relative to the sync mark), covered topics, uncovered topics (for a next episode), 8–12 title candidates in varied styles, a short description (≤ 300 chars), a long description (≤ 1500 chars), two social post variants, key quotes with timestamps, mentions grouped by kind with a URL when the model is confident and a `TODO link` line with a search query when not, promises made on air ("we'll put the link in the notes").
- **FR-31** `livecaster wrapup <session_dir>` re-runs the wrap-up on an existing session without audio (for retries or a stronger model).

### Developer mode

- **FR-32** `livecaster replay <file.wav | session_dir> [--speed 4] [--mock-llm]` runs the same pipeline from a file. `--mock-llm` uses canned responses from a fixtures directory so the UI and reducer can be developed offline.
- **FR-33** `livecaster check` verifies: Venice key and model availability, STT model download and a 3-second self-test, audio devices, AudioTee availability (macOS).

## 5. Non-functional requirements

| Area | Requirement |
|---|---|
| Latency | Utterance end → transcript in UI ≤ 2 s (M1, Parakeet). Tick p95 ≤ 10 s with reasoning effort `low`; if measured higher, default to `none`. UI patch applied ≤ 100 ms after receipt. |
| Throughput | Two channels of continuous speech on an M1 without the STT queue growing. Queue depth is visible in the UI. |
| Robustness | STT crash → restart the engine, keep audio buffered up to 60 s. LLM/network failure → transcription continues, backoff, UI shows status. Process crash → nothing lost except the in-flight utterance; resume works. |
| Privacy | Audio never leaves the machine. Only transcript text, outline text and derived state go to Venice. Venice's injected system prompt is disabled. Web search is off for ticks. |
| Cost | Target < $1 per 2-hour episode with `deepseek-v4-flash-0731` (see §11). |
| Dependencies | No PyTorch on macOS. Prefer onnxruntime and MLX. Keep the install to `uv sync` plus one optional helper build. |
| Code quality | Type hints, pydantic v2 models for all data crossing a boundary, ruff clean, pytest. |

## 6. Architecture

```
                      ┌──────────────────────────────────────────────────────────────┐
                      │  Python process (uv run livecaster)                          │
  mic ──sounddevice──►│ AudioSource(Host) ─► VAD/Segmenter ─┐                        │
  browser ─AudioTee──►│ AudioSource(Guest)─► VAD/Segmenter ─┼─► STT worker (1 thread) │
  file ──────────────►│ AudioSource(File) ─► VAD/Segmenter ─┘        │               │
                      │        │ (WAV recorder per channel)          ▼               │
                      │        ▼                              transcript segments    │
                      │  sessions/<id>/audio/*.wav                    │               │
                      │                                               ▼               │
                      │   ┌──────────────── asyncio main loop ─────────────────────┐ │
                      │   │ SessionStore (state, reducer, persistence)             │ │
                      │   │ FastLane (trigger matching)                            │ │
                      │   │ Reasoner (tick scheduler → Venice → reducer)           │ │
                      │   │ Wrapup (final analysis → exports)                      │ │
                      │   │ FastAPI + WebSocket broadcast                          │ │
                      │   └────────────────────────────────────────────────────────┘ │
                      └──────────────────────────────┬───────────────────────────────┘
                                                     │ ws://127.0.0.1:8765/ws
                                            ┌────────▼─────────┐
                                            │ Browser UI       │  (laptop, or tablet on LAN)
                                            └──────────────────┘
```

Threads and loops: PortAudio callbacks run on PortAudio's thread and only copy frames into a per-channel queue. One segmenter thread per channel runs VAD and emits utterances into a single STT queue. One STT thread owns the model (MLX and ONNX sessions are not thread-safe) and hands results to the asyncio loop with `call_soon_threadsafe`. Everything stateful lives on the asyncio loop.

Modules (see the plan for the file layout): `outline`, `audio`, `stt`, `llm`, `session`, `server`, `ui`, `replay`, `cli`, `config`.

## 7. Component specifications

### 7.1 Outline parser (`outline/`)

Input: UTF-8 Markdown text (decode with `errors="replace"`). Normalize CRLF to LF. Strip C0 control characters except tab and newline, and ANSI CSI escape sequences. Tabs count as 4 spaces for indentation.

Recognized constructs, in order of precedence per line:

| Construct | Rule | Node kind | Coverable |
|---|---|---|---|
| YAML front matter | `---` on line 1 … `---` | `meta` (key/values) | no |
| Horizontal rule | `^\s*(-{3,}|\*{3,}|_{3,})\s*$` | ignored | — |
| Heading | `^#{1,6}\s+` | `heading` (level) | only if it ends up with no coverable descendants |
| Fenced code | ```` ``` ```` … ```` ``` ```` | `paragraph` (verbatim) | no |
| List item | `^(\s*)([-*+]|\d+[.)])\s+` | `item`; depth from indentation relative to the enclosing item | yes |
| Question line | text (after bullet or at line start) matching `^\**\s*(otázka|otazka|question|q)\s*:?\**\s*:?` case-insensitively | `item` with `kind=question` | yes |
| Metadata line | before the first level-2 heading, `^\*\*[^*]+:\*\*` or `^[\w\s]+:\s` | `meta` | no |
| Continuation | indented text line right after an item, not a new item | appended to the item text | — |
| Paragraph | any other non-empty line; consecutive lines join until a blank line | `paragraph` | yes |

Node model:

```python
class Node(BaseModel):
    id: str  # "T17"
    kind: Literal["heading", "item", "paragraph", "question", "meta"]
    level: int  # heading level, or nesting depth for items (0-based)
    parent: str | None
    children: list[str]
    text_md: str  # inline markdown preserved
    text: str  # plain text (markdown stripped), single line, whitespace collapsed
    links: list[str]  # URLs found in the node
    line_start: int
    line_end: int
    coverable: bool
```

IDs are assigned in document order starting at `T1`. On re-parse (FR-04), match old→new nodes by `normalize(text)` (casefold, strip punctuation, collapse whitespace); exact matches keep their ID and state; unmatched old nodes are retired (state kept in `retired_nodes` for the export); new nodes get fresh IDs continuing the sequence, never reusing a retired number.

Rendering for the LLM (compact, one node per line, indentation shows structure):

```
[T1] # Osnova podcastu s Zuzkou (dychova-praca.example) — Podcast o všeličom
[T2] meta Cieľ: Spojiť témy dychu, nervového systému, slobody ...
[T3] ## Úvod / kontext
[T4]   - Prečo práve teraz dych? (Juraj + Zuzka mali session)
[T5]   - Krátko o Zuzke: RYT 500, pránájáma, vagus, stres/úzkosť/nespavosť
...
[T31]  - Diagnostika + "manuál údržby" — vstupná diagnostika ...
[T32]    - Prečo sa venovať dychu a nie miliónu iných vecí?
...
[T58] ¶ Počas dychového cvičenia bolo dosť intenzívne. Mal som pocit ...
```

Paragraphs are sent in full (they are the host's own notes and matter for reasoning). Statuses are **not** included in this block so it stays byte-identical across ticks (prompt caching); they go in the dynamic part of the prompt.

Rendering the annotated outline (export): reproduce the original file line by line; for covered leaves wrap the text in `~~…~~` and append ` ✅ HH:MM:SS`; touched leaves append ` ◐`; skipped append ` ⏭`; headings append ` (3/7)`. Append a section with new topics that were discussed but not in the outline. Everything else, including metadata and blank lines, is copied verbatim.

Test fixture: `osnova.md` from this repository must parse into (approximately) 1 level-1 heading, 8 level-2 headings, ~45 items including 6 nested ones and 2 question lines, 6 paragraphs in section 6, and 2 meta lines; the stray `[118;1:3u` characters in section 6 remain harmless text.

### 7.2 Audio capture (`audio/`)

```python
class AudioSource(Protocol):
    name: str
    def start(self, on_frames: Callable[[np.ndarray, float], None]) -> None  # float32 mono 16 kHz, session time of first sample
    def stop(self) -> None
```

Implementations:

- `DeviceSource(device, channel_index=None, sample_rate=None)` using `sounddevice.InputStream`. Ask PortAudio for 16 kHz; if the device refuses, open at the native rate and resample with `soxr`. `channel_index` picks one channel of a multi-channel interface; `None` averages all channels.
- `AudioTeeSource(include_processes=[pid...] | app_name)` (macOS ≥ 14.2). Spawns the `audiotee` binary with `--sample-rate 16000 --chunk-duration 0.1 --include-processes <pids>` and reads int16 PCM from stdout. Resolves an app name (e.g. "Google Chrome", "Safari", "Zencastr") to PIDs via `pgrep -x`/`NSRunningApplication`-equivalent at start and re-resolves if the process disappears. Logs stderr. First run triggers the macOS "system audio recording" permission prompt for the terminal app; the check command must explain this.
- `LoopbackDeviceSource` is just `DeviceSource` pointed at BlackHole (macOS) or a `.monitor` source (Linux PipeWire/Pulse); no special code.
- `FileSource(path, speed=1.0)` for replay: decodes with `soundfile` (or ffmpeg for non-WAV), emits frames paced at `speed × real time`; `speed=0` means as fast as possible.

Recorder: per channel, an `soundfile` writer of 16 kHz mono 16-bit WAV, flushed every second. Optional `record_native=true` writes the native-rate stream too.

Session time: `t = time.monotonic() - session_start`, in seconds, float. Sources stamp frames with the session time of the first sample; the segmenter carries these stamps into segments.

### 7.3 Voice activity detection, segmentation and STT (`audio/vad.py`, `audio/segmenter.py`, `stt/`)

VAD: `pysilero-vad` (Silero v5, onnxruntime), 16 kHz, 32 ms frames (512 samples). Threshold 0.5 speech probability, configurable.

Segmenter state machine per channel: `idle` → (speech frame) → `speech` (keep 300 ms of pre-roll) → (≥ 600 ms of non-speech) → emit utterance if speech duration ≥ 300 ms → `idle`. Hard cut at 20 s: cut at the lowest-probability frame in the last 3 s, emit, continue. Utterances carry `(channel, t0, t1, audio: np.ndarray)`.

```python
class STTEngine(Protocol):
    name: str
    languages: set[str] | None   # None = open set (Whisper)
    def warmup(self) -> None
    def transcribe(self, audio: np.ndarray, language: str | None) -> STTResult

class STTResult(BaseModel):
    text: str
    language: str | None
    words: list[Word]          # (start, end, text) relative to the utterance, may be empty
    confidence: float | None
```

Engines and defaults:

| Engine key | Package | Model | Platform | Slovak | Notes |
|---|---|---|---|---|---|
| `parakeet-mlx` | `parakeet-mlx` | `mlx-community/parakeet-tdt-0.6b-v3` | macOS Apple Silicon | yes (25 EU languages incl. en, sk, cs) | **Default on macOS, confirmed by the host.** Word timestamps, punctuation, fast. Auto-detects language among its 25; cannot be forced. Use `model.transcribe(audio_array)`; the streaming `transcribe_stream` API is reserved for the optional live-preview feature. |
| `onnx-asr` | `onnx-asr` | `nemo-parakeet-tdt-0.6b-v3` | Linux (CPU/CUDA), also macOS CPU | yes | **Default on Linux.** `onnx_asr.load_model("nemo-parakeet-tdt-0.6b-v3")`, then `.recognize(waveform)`. Real-time capable on CPU for utterance-sized chunks. |
| `whisper-mlx` | `mlx-whisper` | `mlx-community/whisper-large-v3-turbo` | macOS | yes (99 languages) | Fallback when the language is outside Parakeet's set, or when Parakeet's Slovak quality disappoints. Accepts numpy arrays; supports `language=` forcing. |
| `faster-whisper` | `faster-whisper` | `large-v3-turbo` | Linux (CUDA/CPU), macOS CPU | yes | Fallback on Linux. |

Not chosen: Qwen3-ASR (no Slovak), Voxtral Realtime (13 languages, no Slovak), whisper.cpp (viable but adds a native build; may be added later behind the same interface).

Engine selection: `stt.engine = auto` picks `parakeet-mlx` on macOS-arm64 and `onnx-asr` elsewhere, then falls back to the Whisper engine if the configured `language` is not in the engine's set. Models are downloaded on first use to the Hugging Face cache; `livecaster check` pre-downloads.

Text post-processing: trim, collapse whitespace, drop segments that are empty or consist only of filler (`hm`, `mhm`, `ehm`, `uh`), drop Whisper hallucination patterns (repeated phrases 3+ times, known artifacts such as "Titulky vytvořil…").

### 7.4 Session state (`session/`)

```python
class NodeState(BaseModel):
    id: str
    status: Literal["untouched", "touched", "covered", "skipped"] = "untouched"
    covered_at: float | None  # session seconds
    evidence: list[Evidence] = []  # (t, quote, segment_ids, source: "llm"|"manual")
    hot: HotInfo | None  # (score, reason, segue, since_t, rank)
    warm: float = 0.0  # fast-lane score, decays
    pinned: bool = False
    manual_lock_until: float | None  # LLM may not change status before this session time


class Segment(BaseModel):
    id: str
    channel: str
    speaker: str | None
    t0: float
    t1: float  # speaker None in single-mic mode
    text: str
    language: str | None
    words: list[Word]
    engine: str


class Mention(BaseModel):
    id: str
    kind: Literal[
        "person",
        "book",
        "article",
        "link",
        "tool",
        "product",
        "place",
        "event",
        "concept",
        "promise",
        "other",
    ]
    text: str
    context: str
    first_t: float
    segment_ids: list[str]
    url: str | None
    needs_link: bool
    search_query: str | None


class Suggestions(BaseModel):
    current: CurrentTopic | None  # node_id or None, one-line summary
    next: list[HotInfoRef]  # ordered, max 5
    questions: list[Question]  # max 5
    new_topics: list[NewTopic]  # title, summary, since_t


class Session(BaseModel):
    id: str
    created_at: datetime
    outline_path: str
    mode: Literal["live", "remote", "replay"]
    language: str | None
    language_votes: dict[str, int]
    channels: list[ChannelConfig]
    sync_marks: list[float]  # session seconds
    outline: list[Node]
    retired_nodes: dict[str, NodeState]
    nodes: dict[str, NodeState]
    suggestions: Suggestions
    mentions: list[Mention]
    preflight: Preflight | None
    usage: Usage  # prompt/completion/cached tokens, cost USD, tick count, failures
    status: Literal["idle", "running", "paused", "finishing", "finished"]
```

Transcript segments are not part of `state.json`; they live in `transcript.jsonl` and in memory.

### 7.5 Reasoner (`llm/reasoner.py`)

Scheduler: an asyncio task that wakes every second and decides whether to tick (FR-17). Inputs to the prompt builder: outline render (cached string), state summary, mentions summary, transcript window, new-part marker (the segment ID of the first segment since the last successful tick), podcast language, session time.

Venice request (OpenAI-compatible, via the `openai` Python client with `base_url` set, or `httpx` directly):

```json
{
  "model": "deepseek-v4-flash-0731",
  "messages": [{"role": "system", "content": "<instructions + outline block>"},
               {"role": "user",   "content": "<state + mentions + transcript window + task>"}],
  "response_format": {"type": "json_schema", "json_schema": {"name": "tick", "strict": true, "schema": { ... }}},
  "temperature": 0.2,
  "max_completion_tokens": 1500,
  "reasoning_effort": "low",
  "prompt_cache_key": "<session id>",
  "venice_parameters": {"include_venice_system_prompt": false, "strip_thinking_response": true, "enable_web_search": "off"}
}
```

Timeout 30 s per tick. On invalid JSON, retry once with the validation error appended; on second failure, log and skip. Prompt caching on Venice is prefix-based and automatic for DeepSeek models above ~1024 tokens: keep the system message byte-identical for the whole session and read `usage.prompt_tokens_details.cached_tokens` to confirm hits.

Token budget: system ≈ instructions 1.0k + outline 2–4k; user ≈ state 0.5k + mentions 0.3k + transcript 2.5k + task 0.2k; output ≤ 1.5k.

### 7.6 Fast lane (`session/fastlane.py`)

On each new segment: lowercase and strip diacritics from both the segment text and each node's trigger phrases (pre-flight) plus the node's own significant words (length ≥ 5, minus a small stop list). Score = fraction of a node's phrases found (whole-word or 85 % fuzzy via `rapidfuzz.partial_ratio`). Nodes with score ≥ 0.5 get `warm = max(warm, score)`; all `warm` values decay by 20 % per minute. `warm` only affects rendering. Zero LLM cost, sub-millisecond.

### 7.7 Wrap-up (`llm/wrapup.py`)

Runs on Finish or via the CLI. Two calls:

1. **Final analysis** (final model, reasoning effort `high`, `max_completion_tokens` 8000): full transcript with timestamps and speakers, outline block, current node states, mentions, new topics, sync offset, podcast language. Returns the schema in §9.4. A two-hour episode is ~30–40k input tokens; the 1M context is not a constraint.
2. **Link resolution** (optional, `wrapup.resolve_links = true`): for mentions with `needs_link`, one call with `venice_parameters.enable_web_search = "on"` asking only for URLs it can cite; anything not cited stays a TODO. Off by default because it is slower and costs more.

Exports are rendered by the app from the JSON with Jinja2 templates; section headings come from the `labels` object the model returns in the podcast language, with an English fallback table.

### 7.8 Web server and UI (`server/`, `ui/`)

FastAPI + uvicorn. Endpoints: `GET /` (index with build ID injected), `GET /static/*`, `GET /api/state` (full session JSON), `GET /api/transcript` (JSONL stream), `POST /api/control` (same actions as the WebSocket), `WS /ws`.

WebSocket protocol (JSON messages, `type` field):

Server → client
- `hello {build_id, session_id, config_summary}`
- `state {session}` full snapshot on connect and after outline reload
- `patch {nodes: {id: NodeState}, suggestions?, mentions?, usage?}` after ticks and manual actions
- `segment {Segment}` for each transcript segment
- `status {audio: {channel: level_db}, stt: {engine, queue, last_latency_ms}, llm: {state, last_tick_t, last_latency_ms, next_in_s, error?}, clock: t}` at 5 Hz
- `toast {level, text}`

Client → server
- `mark {node_id, status}` (`covered | untouched | skipped`)
- `pin {node_id, pinned}`
- `sync_mark {}`
- `control {action}` (`start | pause | resume | finish | tick_now | reload_outline`)
- `select {node_id}` (for keyboard navigation state, optional)

UI implementation: `ui/index.html`, `ui/app.js`, `ui/styles.css`, vendored `marked.min.js` (MIT) for inline Markdown in node text. State is a single client-side object; rendering is a full re-render of the outline list via DOM diffing by node ID (keyed elements), which is fast enough for a few hundred nodes. See Appendix C for the layout.

## 8. Session directory

```
sessions/2026-08-31_demo/
  session.json          # Session snapshot (state), rewritten ≤ 1 s after any change (atomic rename)
  outline.md            # copy of the outline at session start (+ outline.<n>.md on each reload)
  transcript.jsonl      # one Segment per line, append-only
  llm.jsonl             # one line per LLM call: kind, request (messages + params), response, usage, latency, cost
  events.jsonl          # manual actions, sync marks, start/pause/finish, errors
  audio/host.wav        # 16 kHz mono backup per channel (optional)
  audio/guest.wav
  final/show_notes.md
  final/outline_annotated.md
  final/transcript.md
  final/transcript.srt
  final/final_analysis.json
```

Slug = outline file stem, or `--slug`. `--resume <dir>` reloads `session.json`, `transcript.jsonl` and continues (audio WAVs are appended as new files with a suffix).

## 9. LLM contracts

### 9.1 Principles

- The LLM proposes, the app disposes. All status changes go through the reducer with thresholds; the LLM never sees itself as authoritative.
- Non-linear by design: the instructions say explicitly that order in the outline is irrelevant and that the best next topic is the one with the most natural bridge from what was just said.
- Language: all human-facing strings the model returns (reasons, segues, questions, titles, descriptions, labels) are in the podcast language. Instructions are in English; the model is told the language code and shown the transcript, which anchors it.
- Evidence: every `covered` claim carries a short verbatim quote from the transcript so the host (and the tests) can check it.
- Conservative coverage: "mentioned in passing" is `touched`; `covered` requires the substance of the item to have been discussed.

### 9.2 Tick output schema

```json
{
  "type": "object", "additionalProperties": false,
  "required": ["language", "current", "covered", "touched", "hot", "questions", "mentions", "new_topics"],
  "properties": {
    "language": {"type": "string", "description": "ISO 639-1 code of the conversation language"},
    "current": {"type": "object", "additionalProperties": false, "required": ["node_id", "summary"],
      "properties": {"node_id": {"type": ["string", "null"]}, "summary": {"type": "string", "maxLength": 200}}},
    "covered": {"type": "array", "maxItems": 12, "items": {"type": "object", "additionalProperties": false,
      "required": ["id", "confidence", "evidence", "t"],
      "properties": {"id": {"type": "string"}, "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                     "evidence": {"type": "string", "maxLength": 240}, "t": {"type": "number"}}}},
    "touched": {"type": "array", "maxItems": 12, "items": {"type": "object", "additionalProperties": false,
      "required": ["id", "note"], "properties": {"id": {"type": "string"}, "note": {"type": "string", "maxLength": 160}}}},
    "hot": {"type": "array", "maxItems": 5, "items": {"type": "object", "additionalProperties": false,
      "required": ["id", "score", "reason", "segue"],
      "properties": {"id": {"type": "string"}, "score": {"type": "number", "minimum": 0, "maximum": 1},
                     "reason": {"type": "string", "maxLength": 200}, "segue": {"type": "string", "maxLength": 240}}}},
    "questions": {"type": "array", "maxItems": 5, "items": {"type": "object", "additionalProperties": false,
      "required": ["text", "node_id", "why"],
      "properties": {"text": {"type": "string", "maxLength": 240}, "node_id": {"type": ["string", "null"]}, "why": {"type": "string", "maxLength": 160}}}},
    "mentions": {"type": "array", "maxItems": 10, "items": {"type": "object", "additionalProperties": false,
      "required": ["kind", "text", "context", "url", "needs_link", "search_query"],
      "properties": {"kind": {"type": "string", "enum": ["person", "book", "article", "link", "tool", "product", "place", "event", "concept", "promise", "other"]},
                     "text": {"type": "string", "maxLength": 120}, "context": {"type": "string", "maxLength": 240},
                     "url": {"type": ["string", "null"]}, "needs_link": {"type": "boolean"}, "search_query": {"type": ["string", "null"]}}}},
    "new_topics": {"type": "array", "maxItems": 3, "items": {"type": "object", "additionalProperties": false,
      "required": ["title", "summary"], "properties": {"title": {"type": "string", "maxLength": 120}, "summary": {"type": "string", "maxLength": 300}}}}
  }
}
```

`t` is the session time in seconds of the evidence, read from the transcript timestamps. `url` must be `null` unless the model is confident the URL is real; hallucinated links are worse than none.

### 9.3 Reducer rules

Applied atomically per tick, then persisted and broadcast as one `patch`.

1. Unknown node IDs are logged and ignored.
2. A node whose `manual_lock_until > now` is not changed by the LLM.
3. `covered` entries: if the node is a heading with children, apply to all its coverable leaves only if `confidence ≥ 0.85`; otherwise ignore. For leaves: `confidence ≥ cover_threshold (0.7)` → `covered` (set `covered_at = t`, append evidence); `≥ touch_threshold (0.4)` and status `untouched` → `touched`. Never downgrade `covered` or change `skipped`.
4. `touched` entries: `untouched` → `touched`.
5. Hot: build the new set from entries whose node is not `covered`/`skipped`. For previous hot nodes absent from the new set, halve the score and drop below 0.2. Pinned nodes always have score 1.0. Keep the top 5, ranked by score; ranks 1–3 get keycaps.
6. Questions: replace with the new list (≤ 5), dedupe against the previous list by `rapidfuzz.ratio ≥ 85`, in which case keep the older `first_seen`.
7. Mentions: key = normalized text (casefold, no diacritics, no punctuation). New key → append with `first_t = now of the new part start`. Existing key → keep the earliest `first_t`, update `context` if longer, set `url` if previously null, `needs_link = needs_link and not url`.
8. New topics: append unless `rapidfuzz.ratio(title, existing) ≥ 80`.
9. Language: increment `language_votes[language]`; `session.language = argmax` unless configured explicitly.
10. Current topic: replace.
11. Usage: add prompt/cached/completion tokens; cost from the model's pricing table (§11).

### 9.4 Final analysis output schema (abridged; full schema in `llm/schemas.py`)

```json
{
  "language": "sk",
  "labels": {"summary": "Zhrnutie", "chapters": "Kapitoly", "covered": "Prebrali sme", "uncovered": "Nestihli sme",
             "titles": "Návrhy názvov", "description_short": "Krátky popis", "description_long": "Dlhý popis",
             "social": "Príspevky na sociálne siete", "quotes": "Citáty", "mentions": "Spomenuté / odkazy",
             "promises": "Sľuby z nahrávky", "new_topics": "Témy mimo osnovy"},
  "summary": ["...", "..."],
  "chapters": [{"title": "...", "start_t": 0.0, "end_t": 412.5, "node_ids": ["T4", "T5"]}],
  "covered": [{"node_id": "T4", "note": "..."}],
  "uncovered": [{"node_id": "T31", "note": "why it was skipped / worth a future episode"}],
  "titles": [{"text": "...", "style": "descriptive|curiosity|quote|question|short"}],
  "description_short": "≤ 300 chars",
  "description_long": "≤ 1500 chars, paragraphs allowed",
  "social": [{"platform": "x|linkedin|generic", "text": "..."}],
  "quotes": [{"t": 1234.0, "speaker": "Guest", "text": "..."}],
  "mentions": [{"kind": "book", "text": "...", "context": "...", "url": null, "needs_link": true, "search_query": "..."}],
  "promises": [{"t": 2210.0, "text": "pošleme link na štúdiu do popisu"}],
  "new_topics": [{"title": "...", "summary": "..."}]
}
```

Chapter times are session seconds; the renderer subtracts the last sync mark (clamped at 0) and formats `HH:MM:SS`.

### 9.5 Pre-flight output schema (abridged)

```json
{"language": "sk",
 "nodes": [{"id": "T4", "questions": ["...", "..."], "triggers": ["session s Zuzkou", "prečo dych", "breathwork"], "related": ["T58"]}]}
```

## 10. Configuration and CLI

`livecaster.toml` (searched in the working directory, then `~/.config/livecaster/`); environment `VENICE_API_KEY` is required for LLM features; every key can be overridden with `--set section.key=value`.

```toml
[audio]
mode = "live"                # live | remote
record = true
record_native = false
[[audio.channels]]
name = "Host"
source = "device:MacBook Pro Microphone"   # device:<name or index> | audiotee:<app name or pid> | file:<path>
channel_index = 0
[[audio.channels]]
name = "Guest"
source = "audiotee:Google Chrome"          # or "device:BlackHole 2ch"
is_direct = true                           # wins cross-talk dedupe (FR-14)

[stt]
engine = "auto"                            # auto | parakeet-mlx | onnx-asr | whisper-mlx | faster-whisper
model = ""                                 # engine default when empty
language = "auto"                          # auto | sk | cs | en | ...
vad_threshold = 0.5
silence_ms = 600
max_utterance_s = 20

[llm]
base_url = "https://api.venice.ai/api/v1"
tick_model = "deepseek-v4-flash-0731"
final_model = "deepseek-v4-flash-0731"     # deepseek-v4-pro-0813 recommended when available
tick_interval_s = 25
min_new_words = 25
burst_words = 120
transcript_window_words = 1200
reasoning_effort_tick = "low"              # none | low | high
reasoning_effort_final = "high"
temperature = 0.2
cover_threshold = 0.7
touch_threshold = 0.4
manual_lock_minutes = 10
preflight = true

[wrapup]
resolve_links = false

[ui]
host = "127.0.0.1"                         # 0.0.0.0 to reach it from a tablet
port = 8765
open_browser = true
theme = "dark"

[session]
dir = "sessions"
```

CLI (`livecaster`, installed via `uv tool install .` or run with `uv run livecaster`):

- `run <outline.md> [--mode live|remote] [--resume <dir>] [--slug s] [--no-preflight] [--set k=v ...]`
- `devices` — list input devices with indices, channels and default sample rates; on macOS also list candidate AudioTee processes.
- `replay <file.wav|session_dir> --outline <md> [--speed 4] [--mock-llm] [--channel-name Host]`
- `wrapup <session_dir> [--model ...] [--resolve-links]`
- `check` — Venice key, models, STT self-test, devices, AudioTee, permissions.
- `export <session_dir>` — re-render Markdown/SRT from existing JSON without calling the LLM.

## 11. Privacy and cost

Only these leave the machine, and only to Venice: the outline text, transcript text, derived state (node statuses, mentions), and the instructions. Audio and WAV backups never leave. The Venice system prompt injection is disabled; web search is off except for the optional link resolution step. Venice lists the DeepSeek models as `privacy: private` (no retention); an `e2ee-deepseek-v4-flash` variant exists if end-to-end encryption is wanted later, at slightly higher price.

Pricing (Venice, 2026-09-03, USD per 1M tokens): `deepseek-v4-flash-0731` input 0.175, cached input 0.035, output 0.35; `deepseek-v4-flash-0731-fast` 0.35 / 0.0875 / 0.70; `deepseek-v4-pro-0813` 1.65 / 0.165 / 4.95. The app keeps this table in `llm/pricing.py` and refreshes it from `GET /models` at startup when possible.

Estimate for a 2-hour episode at 25 s ticks (~290 ticks, ~8k input of which ~4k cached, ~0.8k output each): about 0.35 USD for ticks plus 0.02 USD (flash) or 0.15 USD (pro) for wrap-up. The `-fast` variant doubles the tick cost if lower latency is needed.

## 12. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Parakeet v3 Slovak accuracy on mixed Slovak/English tech vocabulary | Outline vocabulary is in the LLM context, which tolerates STT errors; Whisper large-v3-turbo fallback behind the same interface; measure WER on a 5-minute sample early (plan M4). |
| Guest voice leaks into the host mic in remote mode (speakers instead of headphones) | Cross-talk dedupe (FR-14); document "use headphones". |
| AudioTee requires a Swift toolchain and a permission prompt that some terminals do not trigger | BlackHole path as the documented fallback; `check` explains the permission; build script in `helpers/`. |
| LLM marks items covered too eagerly | Thresholds, `touched` state, verbatim evidence, manual lock; tune thresholds on replayed sessions. |
| Hallucinated URLs | `url` must be null unless confident; link resolution only with web citations; TODO lines otherwise. |
| Tick latency spikes on Venice | Ticks never overlap; skip rather than queue; `-fast` model option; reasoning effort `none` fallback. |
| Prompt cache misses if the outline changes | Expected and harmless; reload re-establishes the prefix. |
| MLX / onnxruntime wheels vs Python version | Pin Python 3.12; `uv sync` must work on a clean machine. |
| Two channels of continuous speech saturate one STT thread on a slow machine | Queue depth in UI; utterance cap 20 s; option to run Whisper only on the guest channel; Parakeet is several times faster than real time on M1. |

## 13. Assumptions and open questions

Assumptions made in this specification (change them if wrong):

1. Episodes are recorded in Slovak, Czech or English; all three are equally supported and outputs follow the detected language of the episode, never a default.
2. Python + browser UI is acceptable; no native macOS app is expected.
3. The host uses headphones in remote mode.
4. A tick every 25 s is frequent enough; the fast lane covers the gap.
5. The published recording is made by Zencastr or the host's recorder; the app's WAV is a backup.

Open questions (non-blocking, defaults chosen):

- Should the wrap-up default to `deepseek-v4-pro-0813`? Default is flash for cost; the CLI flag exists.
- Should the UI be reachable from a tablet by default? Default is localhost only.
- In-person single-mic diarization: out of scope; the host confirmed that a single feed without speaker distinction is expected in that mode.

## Appendix A — Prompt drafts

### A.1 Tick system prompt (English; the outline block is appended verbatim)

```
You are the live co-pilot of a podcast host. You receive the host's outline (a numbered map of topics, NOT a script), the current state of which topics were already discussed, and the most recent transcript. Your job is to keep the map accurate and to notice opportunities.

Rules:
1. The outline order does not matter. The best next topic is the one with the most natural bridge from what was just said, wherever it sits in the outline.
2. Be conservative with "covered": only when the substance of the item was actually discussed. A passing mention is "touched".
3. Every "covered" entry must quote a short verbatim fragment of the transcript as evidence and give its timestamp.
4. "hot" = topics that became relevant right now because of something said in the NEW part of the transcript. Give a reason and a one-sentence segue the host could say. Skip topics already covered.
5. Suggest at most 5 follow-up questions for the current moment. Short, concrete, in the voice of a curious host.
6. Extract mentions worth linking in the show notes: people, books, articles, tools, products, places, events, and promises like "we will put the link in the description". Set "url" only if you are certain it is a real URL; otherwise null and a search query.
7. If the conversation goes somewhere not in the outline for more than a few sentences, report it in "new_topics".
8. Focus on the NEW part of the transcript; use the earlier part only as context.
8b. Speaker labels may be absent (single shared microphone). Then infer who is speaking only when the content makes it obvious, and never state a speaker as fact.
9. All human-facing text (summary, reasons, segues, questions, notes) must be in the conversation language: {language_name} ({language_code}). Never switch to English unless the conversation is in English.
10. Output only JSON matching the schema.

OUTLINE (id, structure, text):
{outline_block}
```

### A.2 Tick user message

```
SESSION TIME: {hh:mm:ss}
STATE: covered: T4(00:03:12), T5(00:05:40) · touched: T9 · skipped: T30 · pinned: T20 · hot last tick: T18, T22
MENTIONS SO FAR: Wim Hof (person), "Breath" James Nestor (book), ...
NEW TOPICS SO FAR: ...

TRANSCRIPT (older context first, then the NEW part):
[00:11:03 Host] ...
[00:11:20 Guest] ...
--- NEW SINCE LAST TICK ---
[00:12:41 Guest] ...
[00:12:58 Host] ...

TASK: Update the map for the NEW part. Return the JSON.
```

### A.3 Final analysis system prompt (abridged)

```
You are producing show notes for a finished podcast episode. You receive the outline, the full transcript with timestamps and speakers, the live state (covered/touched/skipped), collected mentions and new topics. Write everything in {language_name}. Chapters must follow the actual conversation, with start/end times taken from the transcript, 5–15 chapters, titles short and specific. Titles: 8–12 candidates in varied styles. Descriptions must be publishable as-is. Quotes must be verbatim. For mentions, include a URL only if you are certain it is real; otherwise null plus a search query. Also return "labels": the section headings for the show notes translated to {language_name}. Output only JSON.
```

### A.4 Pre-flight system prompt (abridged)

```
Read the podcast outline. For each coverable node return 2–3 sharp questions the host could ask, 3–6 trigger phrases (words a guest might say that signal this topic; include the conversation language and English variants), and IDs of related nodes elsewhere in the outline. Write in {language_name}. Output only JSON.
```

## Appendix B — Reference data for tests

- `tests/fixtures/osnova.md` — copy of the repository outline.
- `tests/fixtures/transcript_sk.jsonl` — ~60 hand-written Slovak segments (Host/Guest) that walk through sections 0, 1 and 3 out of order and mention "Wim Hof", "Buteyko", "ayahuasca", "Kryptocamp".
- `tests/fixtures/transcript_sk_singlemic.jsonl` — the same conversation with no speaker labels (single-microphone mode).
- `tests/fixtures/transcript_en.jsonl` — ~25 English segments against a small English outline `outline_en.md`, to verify English detection and English outputs.
- `tests/fixtures/transcript_cs.jsonl` — ~15 Czech segments against `osnova.md`, to verify that a Czech guest on a Slovak outline yields consistent outputs.
- `tests/fixtures/tick_responses/*.json` — canned tick outputs for the mock LLM, including one invalid JSON and one with an unknown node ID.
- `tests/fixtures/final_analysis.json` — canned wrap-up output for export rendering tests.
- `tests/fixtures/speech_sk_30s.wav`, `speech_en_30s.wav`, `speech_cs_30s.wav` — 30-second samples (the host records the Slovak and English ones; Czech may come from a public-domain recording) for the STT self-test and replay tests.

## Appendix C — UI layout

```
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│ ● REC 00:42:17   Host ▮▮▮▮▮▯▯  Guest ▮▮▮▯▯▯▯   STT parakeet q:0   LLM ok 6s ago 4.1s   │
│ $0.12 · 38 ticks     [Pause] [Finish]  [Sync mark m]  [Tick now]  [Reload outline]        │
├───────────────────────────────────────────────────────┬──────────────────────────────────┤
│ OUTLINE                                    ▲ 1 hot    │ NOW                              │
│ ## Úvod / kontext                              3/3    │ Fyziológia: CO2 tolerancia …     │
│   ~~Prečo práve teraz dych?~~            ✅ 00:03:12  │                                  │
│   ~~Krátko o Zuzke …~~                   ✅ 00:05:40  │ NEXT                             │
│   ~~Prepojenie na minulé epizódy …~~     ✅ 00:07:02  │ ① Psychedeliká vs. dych          │
│ ## 1. Fyziológia dychu                         2/7    │   hosť spomenul ayahuascu …      │
│   ~~Predĺžený výdych …~~                 ✅ 00:15:10  │   „Keď už si spomenula …“        │
│ ▌ CO2 tolerancia training (Buteyko) ● now             │ ② Nosové dýchanie pri výkone      │
│   Nosové dýchanie aj pri intenzívnom výkone ◐         │ ③ Sauna/dych ako meditácia        │
│   Hlboký nádych ústami …                              │                                  │
│ ## 3. Rituál, komunita a zmenené stavy         0/9    │ QUESTIONS                        │
│ ▌① **Psychedeliká vs. dych** — hosť práve …           │ · Ako spoznáš, že …?             │
│     ↳ „Keď už si spomenula ayahuascu — ako …“         │ · Čo by si poradila …?           │
│   Dych ako zmenený stav vedomia bez chémie            │                                  │
│   …                                                   │ MENTIONS                         │
│ ## 6. Poznámky z prvej session Juraja                    0/6    │ 📖 Breath (James Nestor) 🔗 TODO  │
│   ¶ Počas dychového cvičenia …                        │ 👤 Wim Hof                       │
│                                            ▼ 0 hot    │ 🎪 Kryptocamp 🔗 TODO             │
│                                                       │ [Transcript ▾]                   │
└───────────────────────────────────────────────────────┴──────────────────────────────────┘
```

## Appendix D — References

- Venice API: chat completions (`response_format`, `reasoning_effort`, `venice_parameters`, `prompt_cache_key`), models endpoint with pricing and capabilities, prompt caching guide. https://docs.venice.ai
- DeepSeek V4 Flash 0731 model card: https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash-0731
- Parakeet TDT 0.6B v3 (25 languages incl. Slovak, CC-BY-4.0): https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3
- parakeet-mlx: https://github.com/senstella/parakeet-mlx
- onnx-asr (Parakeet v3 ONNX for CPU/CUDA): https://github.com/istupakov/onnx-asr
- mlx-whisper / mlx-audio STT overview: https://blaizzy.github.io/mlx-audio/models/stt/
- AudioTee (Core Audio process taps CLI, macOS 14.2+): https://github.com/makeusabrew/audiotee
- Core Audio taps background: https://www.recall.ai/blog/core-audio-taps
- BlackHole virtual audio driver: https://github.com/ExistentialAudio/BlackHole
- pysilero-vad: https://github.com/rhasspy/pysilero-vad
