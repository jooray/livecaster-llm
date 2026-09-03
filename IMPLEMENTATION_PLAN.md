# Livecaster — Implementation Plan

Version 0.1 · 2026-09-03 · Companion to `SPEC.md` (requirement IDs `FR-xx` refer to it).

This document is written for the implementation model. Work through the milestones in order. Each milestone lists tasks, files, acceptance criteria and the exact commands that verify it. Stop only at the marked **checkpoints**; everywhere else, decide, record the decision in `DECISIONS.md`, and continue.

## 1. Ground rules

1. **Tooling.** Python 3.12 pinned in `.python-version`; `uv` for everything (`uv sync`, `uv run`, `uv add`). Never install packages outside the project virtual environment. Never use pip globally, pyenv or poetry.
2. **No PyTorch on macOS.** STT and VAD run on MLX or onnxruntime. If a dependency pulls in torch on macOS, find another dependency.
3. **The outline is read-only.** Nothing in the codebase may open the user's outline for writing. Tests assert this.
4. **Audio stays local.** The only network calls are to the Venice base URL (and Hugging Face for model downloads).
5. **State belongs to the app.** LLM output is data that goes through validation and the reducer. No code path lets the model set a status directly.
6. **Quality gate per milestone:** `uv run ruff format --check . && uv run ruff check . && uv run pytest -q` must pass. Type hints everywhere; pydantic v2 models for anything serialized or crossing a thread/process/network boundary.
7. **Git.** The directory is not a repository yet: `git init` in M0. Commit at least once per milestone with a message that names the milestone. `.gitignore` must exclude `sessions/`, `.env`, `.venv/`, `helpers/audiotee/.build/`, `*.wav` outside `tests/fixtures/`.
8. **Secrets.** `VENICE_API_KEY` from the environment or a local `.env` (python-dotenv). Never log it, never commit it.
9. **Docs.** Keep `README.md` current: install, first run, remote-mode setup (BlackHole / AudioTee / Linux monitor), CLI reference. Write `docs/RUNBOOK.md` (pre-show checklist) in M8.
10. **Language.** Code, comments, commit messages and docs in English. Fixture content and UI copy may be Slovak where it mirrors real use. UI labels are English in v1 (the host reads Slovak content inside an English chrome); do not localize the chrome.

## 2. Target repository layout

```
livecaster-llm/
  pyproject.toml            .python-version            README.md            DECISIONS.md
  SPEC.md                   IMPLEMENTATION_PLAN.md     livecaster.toml.example
  osnova.md                 (user's example outline; read-only)
  src/livecaster/
    __init__.py             __main__.py                cli.py               config.py         log.py
    timeutil.py             (session clock, hh:mm:ss formatting, atomic file writes)
    outline/  parser.py  model.py  render.py  remap.py
    audio/    sources.py  vad.py  segmenter.py  recorder.py  devices.py
    stt/      base.py  registry.py  parakeet_mlx.py  onnx_asr.py  whisper_mlx.py  faster_whisper.py  postprocess.py
    llm/      client.py  pricing.py  schemas.py  prompts.py  reasoner.py  preflight.py  wrapup.py  mock.py
    session/  models.py  store.py  reducer.py  fastlane.py  transcript.py  exports.py
    server/   app.py  ws.py  protocol.py  static.py
    ui/       index.html  app.js  styles.css  vendor/marked.min.js
    templates/ show_notes.md.j2  outline_annotated.md.j2  transcript.md.j2  transcript.srt.j2
    replay.py
  helpers/audiotee/         build.sh (clones and builds AudioTee; macOS only)
  tests/
    fixtures/ osnova.md  transcript_sk.jsonl  tick_responses/  final_analysis.json  preflight.json  speech_sk_30s.wav
    test_outline_parser.py  test_outline_remap.py  test_render.py  test_reducer.py  test_prompts.py
    test_schemas.py  test_fastlane.py  test_segmenter.py  test_stt_postprocess.py  test_exports.py
    test_store.py  test_protocol.py  test_replay.py  test_cli.py
    integration/ test_venice_smoke.py (skipped without VENICE_API_KEY)  test_stt_selftest.py (skipped without model)
  sessions/                 (gitignored)
```

## 3. Dependencies

`pyproject.toml` skeleton (adjust versions to what `uv add` resolves on the day; keep the platform markers):

```toml
[project]
name = "livecaster"
version = "0.1.0"
requires-python = ">=3.12,<3.13"
dependencies = [
  "fastapi", "uvicorn[standard]", "pydantic>=2", "httpx", "openai>=1.50",
  "numpy", "sounddevice", "soundfile", "soxr", "pysilero-vad",
  "rapidfuzz", "jinja2", "watchfiles", "typer", "rich", "python-dotenv", "unidecode",
]
[project.optional-dependencies]
mac   = ["parakeet-mlx", "mlx-whisper"]                  # Apple Silicon only
linux = ["onnx-asr[cpu]", "faster-whisper"]
cuda  = ["onnx-asr[gpu]", "faster-whisper"]
[project.scripts]
livecaster = "livecaster.cli:app"
[dependency-groups]
dev = ["pytest", "pytest-asyncio", "ruff", "pyright"]
[tool.ruff]
line-length = 110
target-version = "py312"
```

Install: `uv sync --extra mac` on macOS, `uv sync --extra linux` (or `--extra cuda`) on Linux. Import STT engines lazily inside their modules so the core imports on any platform.

LLM client: use the `openai` client with `base_url="https://api.venice.ai/api/v1"` and pass Venice-specific fields through `extra_body={"venice_parameters": {...}, "prompt_cache_key": ...}`. Set `timeout=30` for ticks and `120` for the wrap-up, `max_retries=1`.

## 4. Milestones

### M0 — Bootstrap

Tasks
1. `git init`; create the layout in §2 with empty modules; `pyproject.toml`; `.python-version` = `3.12`; `.gitignore`; `README.md` with install instructions; `DECISIONS.md` with a first entry.
2. `config.py`: pydantic models mirroring `livecaster.toml` (SPEC §10) with defaults; loader that merges file → env (`LIVECASTER_*` optional) → `--set k=v` overrides; `livecaster.toml.example`.
3. `log.py`: rich logging to stderr plus a rotating file in the session directory once one exists; never log the API key.
4. `cli.py` (typer): commands `run`, `devices`, `replay`, `wrapup`, `export`, `check` as stubs that print "not implemented" except `check`, which in this milestone verifies the Venice key by calling `GET /models` and confirming `tick_model` and `final_model` exist, printing their pricing.
5. `llm/pricing.py`: static table for the three DeepSeek models from SPEC §11 plus a refresh-from-`/models` function.
6. `timeutil.py`: `SessionClock`, `fmt_hms(seconds)`, `atomic_write_json(path, obj)`.

Acceptance
- `uv sync --extra mac` succeeds on a clean checkout (macOS) and `uv sync --extra linux` succeeds on Linux or in a Linux container.
- `uv run livecaster check` reports the two models with prices when `VENICE_API_KEY` is set, and a clear error when not.
- Quality gate passes (there will be a couple of trivial tests: config defaults, `fmt_hms`).

### M1 — Outline parser, remap, renderers

Tasks
1. `outline/model.py`: `Node` (SPEC §7.1), `Outline` (nodes in order, index by ID, `leaves()`, `children_of()`, `coverable_leaves_under(id)`).
2. `outline/parser.py`: `parse_outline(text: str) -> Outline` implementing the table in SPEC §7.1. Handle: tabs, CRLF, control characters, front matter, HR, fenced code, nested bullets (tab and space indentation), numbered lists, question lines (`Otázka:`, `**Otázka:**`, `Question:`, `Q:`), metadata lines before the first `##`, continuation lines, paragraphs. Extract links. Produce `text` (plain) with a small inline-markdown stripper (bold, italic, code, links → text).
3. `outline/render.py`: `render_for_llm(outline) -> str` (compact block, SPEC §7.1) and `render_annotated(outline, states, retired, new_topics, original_text) -> str` (line-by-line reproduction with markers).
4. `outline/remap.py`: `remap(old: Outline, new: Outline, states) -> (states, retired)` by normalized text (FR-04).
5. Tests with `tests/fixtures/osnova.md` (copy it there): node counts per kind, nesting of the `Prečo sa venovať dychu…` sub-item under `Diagnostika + "manuál údržby"`, the two question lines in section 4, section 6 paragraphs as separate coverable nodes, links extraction, ID stability across an edit that inserts one bullet in the middle (all other IDs unchanged), annotated render round-trip (unchanged file renders byte-identical when no states are set).

Acceptance
- `uv run python -m livecaster.outline.render osnova.md` prints the LLM block; a human can read it and every bullet of the original is present exactly once.
- Parser never raises on arbitrary text (fuzz test with 200 random line mixes).

### M2 — LLM core on replayed transcripts (no audio, no UI)

Tasks
1. `session/models.py`: all models from SPEC §7.4. `session/store.py`: in-memory session + persistence (`session.json` atomic snapshot debounced 1 s, `transcript.jsonl`, `llm.jsonl`, `events.jsonl` appends), `create_session(outline_path, mode, slug)`, `load_session(dir)`.
2. `session/transcript.py`: transcript buffer with `append(segment)`, `window(words=N) -> list[Segment]`, `new_since(segment_id)`, word counting.
3. `llm/schemas.py`: pydantic models + exported JSON Schema for tick (SPEC §9.2), pre-flight (§9.5) and final analysis (§9.4). A test asserts the exported schema has `additionalProperties: false` everywhere and every property is required (strict mode requirement).
4. `llm/prompts.py`: builders for tick, pre-flight, final (Appendix A). Token budgeting: estimate tokens as `len(text) / 3.2`; trim the transcript window from the front, whole segments only, so the user message stays under `transcript_window_words` words. Keep the system message byte-identical between ticks (test: two builds with different state produce identical system messages).
5. `llm/client.py`: `LLMClient.complete_json(kind, messages, schema, model, effort, max_tokens, cache_key) -> (parsed, usage, latency)`; writes every call to `llm.jsonl`; one retry on validation error with the error appended to the user message; cost from `pricing.py` using `prompt_tokens`, `prompt_tokens_details.cached_tokens` and `completion_tokens`.
6. `llm/mock.py`: `MockLLM` returning canned responses from a directory in order (looping), used by `--mock-llm`.
7. `session/reducer.py`: `apply_tick(session, result, now) -> Patch` implementing SPEC §9.3 exactly; `apply_manual(session, action) -> Patch`; `apply_preflight`.
8. `llm/reasoner.py`: scheduler task (FR-17) with backoff; `tick_now()`; status object for the UI.
9. `llm/preflight.py`: single call at session start when `llm.preflight` is true; stores triggers/questions/related on the session.
10. `replay.py` (transcript mode): reads a `transcript.jsonl` and feeds segments to the store at `speed ×` real time (`speed=0` = instant), running the reasoner. `livecaster replay --transcript tests/fixtures/transcript_sk.jsonl --outline demo/demo.md --mock-llm --speed 0` prints each patch as a compact diff and writes a session directory.
11. Fixtures: write `tests/fixtures/transcript_sk.jsonl` (≈60 Slovak segments, Host/Guest, walking through sections 0, 1 and 3 out of order, mentioning Wim Hof, Buteyko, ayahuasca, Kryptocamp, and one promise "pošleme link do popisu"), `transcript_sk_singlemic.jsonl` (same, no speaker labels), `transcript_en.jsonl` with a small `outline_en.md`, `transcript_cs.jsonl`, and `tick_responses/` (at least 6 files: normal progression, one invalid JSON, one with an unknown ID, one that re-covers a manually uncovered node inside the lock window, one with a heading covered at 0.9, one with hot items only).
12. `tests/integration/test_venice_smoke.py`: real call with the fixture transcript's first 20 segments; asserts valid JSON, `language == "sk"`, at least one hot or covered entry, and that a second identical-prefix call reports `cached_tokens > 0` (mark this last assertion as expected-failure if Venice does not report it for this model; record the observation in `DECISIONS.md`).

Acceptance
- Replay with the mock LLM ends with sections 0 and 1 mostly covered, section 3 item `Psychedeliká vs. dych` hot after the ayahuasca segment, mentions containing Wim Hof and Buteyko, one promise, and `session.json` on disk that `load_session` reads back equal.
- Reducer tests cover every rule in SPEC §9.3, including lock, heading expansion, hot decay and pinned persistence.
- Replay of the English fixture yields `language == "en"` and English suggestions; replay of the single-mic fixture works with no speaker labels anywhere in prompts or exports.
- Real Venice smoke test passes; tick latency and cached-token behaviour are recorded in `DECISIONS.md` (this decides the default `reasoning_effort_tick`: keep `low` if p95 ≤ 10 s over 10 calls, else `none`).

### M3 — Web server, UI, fast lane

Tasks
1. `server/protocol.py`: pydantic models for every WebSocket message (SPEC §7.8). `server/ws.py`: connection manager with a single broadcast queue; slow clients (queue > 200) are disconnected. `server/app.py`: FastAPI app, routes, static files with `Cache-Control: no-store`, `index.html` served with `{{BUILD_ID}}` replaced by a hash of the `ui/` directory computed at startup (FR-27).
2. `session/fastlane.py` (FR-16) with tests on the Slovak fixture (diacritics-insensitive matching: "ayahuasca" and "psychedelika" warm T-psychedeliká; nothing warms on a filler sentence).
3. UI (`ui/`): implement Appendix C. Requirements: keyed rendering by node ID; state classes `untouched|warm|touched|hot|covered|skipped|pinned|current|selected`; heading coverage fraction; hot rank keycaps and reason/segue lines; edge indicators for off-screen hot items; side panel tabs Now / Questions / Mentions / Transcript / New topics; top bar with meters, statuses, cost, buttons; keyboard map (FR-25); dark/light toggle stored in `localStorage`; min font 18 px; reconnecting WebSocket with exponential backoff; reload on build ID mismatch; toasts.
4. Wire manual actions to the reducer and broadcast patches. Wire `control` actions (start/pause/resume/finish stubs; finish calls the wrap-up in M6).
5. Replay drives the UI: `livecaster replay --transcript … --outline demo/demo.md --mock-llm --speed 4` opens the browser and the map fills in over ~1 minute.
6. `test_protocol.py` (message validation), a Playwright-free smoke test using `httpx` against the ASGI app (`/`, `/api/state`, WebSocket hello via `websockets` client).

Acceptance
- With replay at speed 4: strikethroughs, hot highlight with reason and segue, questions and mentions appear; clicking a covered item un-covers it and the next mock tick that re-covers it is ignored (lock); pressing `m` logs a sync mark; edge indicator shows when a hot item is scrolled out of view.
- Editing `ui/app.js` and restarting the server makes an already-open tab reload itself within 5 s of reconnecting.
- **Checkpoint 1:** show the UI running on replay to the human and collect layout feedback before M4.

### M4 — Audio capture, VAD, STT, single-channel live mode (macOS first)

Tasks
1. `audio/devices.py`: enumerate devices; `livecaster devices` prints index, name, max input channels, default sample rate, and marks the system default.
2. `audio/sources.py`: `DeviceSource`, `FileSource` (SPEC §7.2); 16 kHz mono float32 frames of 512 samples with session timestamps; resampling via `soxr` when needed.
3. `audio/vad.py`: thin wrapper over `pysilero-vad` returning speech probability per 512-sample frame. `audio/segmenter.py`: state machine from SPEC §7.3 with pre-roll and hard cut; unit tests with synthetic audio (tone bursts separated by silence) asserting utterance count and boundaries within ±50 ms.
4. `audio/recorder.py`: per-channel WAV writer (FR-09), flushed each second, closed on stop, appended with a numeric suffix on resume.
5. `stt/base.py`, `stt/registry.py` (auto-selection rules from SPEC §7.3), `stt/parakeet_mlx.py`, `stt/whisper_mlx.py`, `stt/postprocess.py` (filler and hallucination filters, tests). STT worker thread with a queue; results marshalled to the asyncio loop; queue depth and last latency exposed for `status`.
6. `livecaster check`: STT self-test on `tests/fixtures/speech_sk_30s.wav` (the human records this file; until then use any short Slovak or English WAV and note it) printing the transcript and real-time factor; audio device sanity (opens the configured device for 1 s).
7. `livecaster run outlines/osnova.md --mode live` end to end with one channel: Start captures, segments flow into the UI, ticks run against Venice, `Pause` stops capture without ending the session, `Finish` stops everything (wrap-up arrives in M6).
8. Level meters (FR-08) from RMS per 100 ms block, sent in `status`.
9. `replay --wav file.wav` path through `FileSource` (real STT, real or mock LLM).

Acceptance
- `livecaster replay --wav your-sample.wav --outline demo/demo.md --mock-llm --speed 0` yields a transcript whose text a Slovak reader recognizes as the recording; real-time factor printed and < 0.3 on M1 with Parakeet.
- Live mode: speaking a sentence shows it in the transcript panel within 2 s of stopping; the WAV backup plays back correctly; a Venice outage (unplug the network) shows an LLM error status while transcription keeps running, and ticks resume when the network returns.
- **Checkpoint 2:** the human reads a 5-minute live test transcript in Slovak and a shorter one in English (Czech if a speaker is available) and confirms Parakeet quality. Parakeet is the confirmed default; `whisper-mlx` stays a fallback that is selected only per language or on explicit configuration. Record observations.

### M5 — Remote mode (two channels), Linux path

Tasks
1. `AudioTeeSource` (SPEC §7.2): `helpers/audiotee/build.sh` (clone `makeusabrew/audiotee`, `swift build -c release`, copy the binary to `helpers/audiotee/bin/`), process resolution by app name, stdout PCM reader, stderr logging, restart on exit, permission guidance in `check`. Document the BlackHole alternative (`source = "device:BlackHole 2ch"` plus a Multi-Output Device so the host still hears the guest).
2. Multi-channel plumbing: one segmenter thread per channel, one STT queue, speaker label = channel name, `is_direct` flag; cross-talk dedupe (FR-14) in `session/transcript.py` with tests (overlapping near-identical texts → keep the direct channel's segment).
3. Sync mark (FR-10): key `m` and button; stored in `events.jsonl` and `session.sync_marks`; shown in the top bar as "sync 00:00:42".
4. Linux: `stt/onnx_asr.py` and `stt/faster_whisper.py`; registry defaults; README section for PipeWire/Pulse monitor sources (`pactl list sources short`); verify in a Linux container or VM that `uv sync --extra linux`, `livecaster check` and `replay --wav` work (CPU).
5. `livecaster.toml.example` with a complete remote-mode example for Chrome + microphone.

Acceptance
- Remote test: play a Slovak podcast episode in Chrome while the host speaks into the mic; the transcript shows `Guest` segments from the browser audio and `Host` segments from the mic; with headphones off, duplicate guest lines from the mic are suppressed by the dedupe rule most of the time (log the residual rate).
- Linux: replay from WAV produces a transcript with `onnx-asr`; documented in README.
- **Checkpoint 3 (before starting M5):** the human installs BlackHole or builds AudioTee and grants the system-audio permission to the terminal app.

### M6 — Wrap-up and exports

Tasks
1. `llm/wrapup.py`: final analysis call (SPEC §7.7 and §9.4), optional link resolution with `enable_web_search = "on"` and `enable_web_citations = true` (only accept URLs that appear in citations), retry once on schema failure, save `final/final_analysis.json`.
2. `session/exports.py` + Jinja2 templates: `show_notes.md` (sections in the order of FR-30, headings from `labels`, chapter times relative to the last sync mark, mentions grouped by kind with `🔗 url` or `TODO link: <search query>`), `outline_annotated.md` (from M1 renderer), `transcript.md` (speaker-labelled paragraphs with `[HH:MM:SS]` every new speaker or 60 s), `transcript.srt` (segments ≤ 7 s, split on word timestamps when available).
3. `Finish` flow: set status `finishing`, stop capture, flush the STT queue, run a last tick, run the wrap-up, write exports, set `finished`, show a completion card in the UI with the output paths and totals (duration, tokens, cost).
4. CLI `wrapup <dir>` and `export <dir>`.
5. Tests: exports rendered from `tests/fixtures/final_analysis.json` and the Slovak transcript fixture; SRT validity (monotonic, ≤ 7 s, correct format); chapter time offset by a sync mark; `labels` fallback to English when a key is missing.

Acceptance
- Replayed session (mock or real) ends with all five export files; `show_notes.md` is entirely in Slovak when the transcript is Slovak (spot-check by a reader), chapters have sensible boundaries, every mention has a URL or a TODO line, uncovered outline items are listed.
- **Checkpoint 4:** the human reviews `show_notes.md` from a real replayed conversation and gives feedback on tone, title styles and length.

### M7 — Robustness and polish

Tasks
1. Outline hot reload (FR-04) with `watchfiles`, remap, `outline.<n>.md` copies, `state` re-broadcast; test with an edit during replay.
2. `--resume <dir>` (FR-28): reload session and transcript, continue clock from the last segment end, new WAV suffix.
3. STT engine crash recovery: restart the engine once; buffer up to 60 s of audio per channel meanwhile.
4. Cost/usage panel details (per-model breakdown, cached share), tick history sparkline (last 20 latencies).
5. `check` completeness: permissions on macOS (microphone, system audio), AudioTee binary, model cache presence, disk space in `sessions/`.
6. Packaging: `uv tool install .` works; `README.md` covers macOS and Linux; `livecaster --version`.
7. Optional (only if time remains, in this order): live "typing" preview using `parakeet-mlx` `transcribe_stream` draft tokens on macOS; Venice embeddings (`text-embedding-multilingual-e5-large-instruct`) as a second fast-lane signal; `e2ee-deepseek-v4-flash` support.

Acceptance
- Kill the process mid-session and `--resume`: no segment before the kill is lost, ticks continue, the final show notes cover the whole session.
- Editing the outline during a session keeps all statuses of untouched lines and shows the new bullet within 2 s.

### M8 — Field test and runbook

Tasks
1. Run a 20-minute mock podcast (the human plus a friend, or the human against a played-back episode) in the intended mode.
2. Tune `cover_threshold`, `touch_threshold`, `tick_interval_s`, `transcript_window_words` from the recorded `llm.jsonl` (replay the session with `--mock-llm` from its own responses to iterate on the reducer without paying for calls).
3. Write `docs/RUNBOOK.md`: pre-show checklist (devices, `check`, headphones, Zencastr sync mark), during-show keys, post-show steps, where the outputs are.
4. Record known issues in `README.md`.

Acceptance
- The human confirms the tool is usable for the next real episode without reading the code.

## 5. Design notes the implementer will need

### 5.1 Tick scheduler

```python
async def run(self):
    while self.running:
        await asyncio.sleep(1)
        if self.in_flight or self.session.status != "running":
            continue
        new_words = self.transcript.words_since(self.last_tick_segment_id)
        due = (now() - self.last_tick_at) >= cfg.tick_interval_s and new_words >= cfg.min_new_words
        burst = new_words >= cfg.burst_words
        if not (due or burst or self.tick_requested):
            continue
        if now() < self.backoff_until:
            continue
        self.in_flight = True
        try:
            result, usage, latency = await self.client.complete_json(...)
            patch = apply_tick(self.session, result, now())
            self.last_tick_segment_id = self.transcript.last_id()
            self.last_tick_at = now()
            self.failures = 0
            await self.broadcast(patch)
        except Exception as e:
            self.failures += 1
            self.backoff_until = now() + min(120, 5 * 2 ** (self.failures - 1))
            self.status.error = str(e)
        finally:
            self.in_flight = False
            self.tick_requested = False
```

### 5.2 Segmenter

```python
class Segmenter:
    def push(self, frame: np.ndarray, t: float) -> list[Utterance]:
        p = self.vad(frame)  # speech probability for 32 ms
        self.preroll.append((frame, t))  # deque of ~10 frames
        if self.state == "idle":
            if p >= thr:
                self.state = "speech"
                self.buf = list(self.preroll)
                self.t0 = self.buf[0][1]
                self.silence = 0
                self.speech_frames = 1
                self.min_p_tail = deque(maxlen=94)  # ~3 s
        else:
            self.buf.append((frame, t))
            self.silence = 0 if p >= thr else self.silence + 32
            self.speech_frames += p >= thr
            self.min_p_tail.append((p, len(self.buf) - 1))
            if self.silence >= cfg.silence_ms:
                return self._emit()  # only if speech_frames*32 >= 300 ms
            if (t - self.t0) >= cfg.max_utterance_s:
                return self._emit(cut_at=min(self.min_p_tail)[1])  # continue with the tail
        return []
```

### 5.3 Transcript window and the "new part" marker

`window(words)` returns whole segments from the end until the word budget is reached, always including every segment after `last_tick_segment_id` even if that exceeds the budget (a burst is more important than the older context). The prompt inserts `--- NEW SINCE LAST TICK ---` before the first new segment. Speaker labels: channel name when there is more than one channel; omitted entirely in single-microphone mode (lines render as `[00:12:41] text`).

### 5.4 WebSocket fan-out and patches

The store emits `Patch` objects (changed node states, optional suggestions/mentions/usage). The server serializes each once and pushes it to all client queues. On connect, a client gets `hello` then a full `state`; there is no incremental catch-up, so a reconnecting client always resyncs from `state`.

### 5.5 UI rendering

Keep a `Map<nodeId, HTMLElement>`. On `state`, build all elements once. On `patch`, update only the listed nodes: toggle state classes, update badge text, reason/segue lines, heading fractions (recompute for ancestors of changed nodes). Hot edge indicators are recomputed on `patch` and on `scroll` (throttled). Inline Markdown in node text goes through `marked.parseInline` once at build time.

### 5.6 Atomic persistence

`atomic_write_json` writes to `path.tmp` then `os.replace`. `session.json` writes are debounced (1 s) but forced on `finish`, `pause`, and every 30 s. JSONL appends use a single writer with `flush()` after each line; on macOS `fsync` once per 5 s is enough.

### 5.7 Normalization used for matching

`normalize(s) = unidecode(s).casefold()` then remove punctuation and collapse whitespace. Used by remap (exact match), mentions dedupe (exact match on the key), fast lane (fuzzy), questions/new topics (fuzzy ratio via rapidfuzz).

## 6. Test plan summary

| Layer | What | How |
|---|---|---|
| Unit | Parser, remap, renderers, reducer, prompts, schemas, fast lane, segmenter, post-processing, exports, protocol | `uv run pytest -q`, fixtures in `tests/fixtures/` |
| Replay | End-to-end without audio, mock LLM | `livecaster replay --transcript … --mock-llm --speed 0` in a test that inspects the resulting session directory |
| STT | Engine self-test and real-time factor | `livecaster check`, `tests/integration/test_stt_selftest.py` (skipped when the model is not cached) |
| Venice | Smoke test with real calls | `tests/integration/test_venice_smoke.py` (skipped without key), never in the default `pytest` run |
| Manual | Live single-channel, remote two-channel, resume, outline edit | Checklist in `docs/RUNBOOK.md`; results noted in `DECISIONS.md` |

Recording the samples: `ffmpeg -f avfoundation -i ":0" -t 30 -ar 16000 -ac 1 tests/fixtures/speech_sk_30s.wav` on macOS (the human reads a paragraph of `osnova.md`; repeat in English for `speech_en_30s.wav`).

## 7. Checkpoints (the only places to stop and wait for the human)

1. After M3: UI review on replay.
2. After M4: transcription quality check in Slovak and English (Parakeet is the confirmed default).
3. Before M5: BlackHole installed or AudioTee built; system-audio permission granted.
4. After M6: show notes review.
5. M8: field test.

## 8. Definition of v1

Minimum for the first real in-person episode: M0–M4 plus M6. Minimum for the first Zencastr episode: add M5. M7 and M8 make it dependable.

## 9. Explicitly deferred

Diarization for a single shared microphone; native macOS app; PWA/offline UI; cloud STT; multi-user; automatic publishing of show notes anywhere; editing the outline from the UI.
