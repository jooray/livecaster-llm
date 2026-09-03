# Livecaster runbook

Everything you need on show day, in the order you need it. Nothing here requires reading the code.

## The day before

1. `uv sync --extra mac` (or `--extra linux`) — picks up any dependency changes.
2. `uv run livecaster check` — this downloads the STT model on a fresh machine, which takes a
   few minutes. Do not leave it for five minutes before the recording.
3. If the guest is remote: confirm AudioTee is built (`./helpers/audiotee/build.sh`) **or**
   BlackHole is installed and your Multi-Output Device still exists.
4. Write the outline. Any Markdown works: headings, bullets, nested bullets, `Otázka:` lines,
   whole paragraphs. Livecaster never writes to it.

## Twenty minutes before

```bash
uv run livecaster check          # Venice key, models, STT self-test, devices, permissions
uv run livecaster devices        # confirm the device names in livecaster.toml still match
```

Checklist:

- [ ] `check` prints both models with prices and a green STT self-test.
- [ ] Device names in `livecaster.toml` match what `devices` prints. macOS renames devices when
      you plug things in differently.
- [ ] **Headphones on.** In remote mode, speakers put the guest's voice back into your microphone;
      the cross-talk dedupe catches most of it but not all.
- [ ] Your real recorder (Zencastr, field recorder) is armed. Livecaster's WAV is a backup only.
- [ ] Disk space: `check` prints the free space in `sessions/`.

## Starting

```bash
uv run livecaster run osnova.md --mode remote     # or --mode live
```

The UI opens at <http://127.0.0.1:8765/>. Set `ui.host = "0.0.0.0"` to read it from a tablet.

1. Watch the level meters while you and the guest say a sentence each. Both should move.
2. Press **Start**.
3. **The moment the external recorder starts, press `m`.** Every chapter timestamp in the show
   notes is measured from that mark. Press it again if you got it wrong; the last one wins.

The pre-flight pass (one LLM call, a few seconds) runs before the server starts, so the map already
has trigger phrases when you begin.

## During the show

| Key | What it does |
|---|---|
| `j` / `k` | move the selection |
| `c` | mark the selected item covered (or un-cover it) |
| `x` | skip the selected item |
| `p` | pin it — it stays at the top of **Next** until you cover it |
| `1` `2` `3` | jump to the hot item with that rank |
| `m` | sync mark |
| `t` | show the transcript panel |
| `space` | pause / resume capture |
| `?` | this table, in the app |

What the map shows:

- **struck through** — covered, with the time it happened
- **dotted underline ◐** — touched, mentioned in passing
- **orange bar with ① ② ③** — hot right now, with the reason and a segue you could say
- **faint tint** — the fast lane heard a trigger word; a hint, not a state
- **⏭** — you skipped it
- `3/7` next to a heading — how much of that section is done

The app never scrolls for you. When hot items are off-screen, the edge indicator says
"▲ 2 hot" — click it to jump.

If you un-cover something the model covered too eagerly, the model is locked out of that item for
ten minutes. That is deliberate: it stops the tick loop from arguing with you.

## When something goes wrong

| Symptom | What to do |
|---|---|
| `LLM error` in the top bar | Nothing. Transcription keeps running and ticks resume by themselves (5 s → 120 s backoff). |
| STT queue climbing | You are on a slow machine with two channels. Nothing to do live; the queue drains at the end. |
| Transcript stopped | Check the level meters. If a meter is dead, the device changed — **Pause**, fix it, **Resume**. |
| Wrong topic went hot | Ignore it, or press `x` to skip the item so it stops coming back. |
| The app crashed | `uv run livecaster run osnova.md --resume sessions/<dir>` — everything except the utterance in flight is on disk. |
| You edited the outline mid-show | It reloads within two seconds and keeps every status whose line did not change. |

## Finishing

1. Press **Finish** (not Ctrl-C). The app flushes the STT queue, runs one last tick, then the
   wrap-up. On a two-hour episode that is about a minute.
2. A card shows the duration, tick count, cost and the output paths.

Outputs land in `sessions/<date>_<slug>/final/`:

| File | What it is |
|---|---|
| `show_notes.md` | Summary, chapters, covered and uncovered topics, title candidates, descriptions, social posts, quotes, mentions with links or TODOs, promises |
| `outline_annotated.md` | Your outline, byte for byte, with `~~strikethrough~~ ✅ HH:MM:SS`, `◐`, `⏭` and section fractions |
| `transcript.md` | Speaker-labelled paragraphs |
| `transcript.srt` | Subtitles, cues ≤ 7 s |
| `final_analysis.json` | The raw wrap-up JSON, if you want to re-render |

Chapter times are relative to your sync mark, so they line up with the published audio.

## After the show

```bash
# Better show notes from the same transcript, with a stronger model
uv run livecaster wrapup sessions/2026-08-31_demo --model deepseek-v4-pro-0813

# Look up the missing URLs (uses web search; slower, costs more)
uv run livecaster wrapup sessions/2026-08-31_demo --resolve-links

# Re-render the Markdown after editing final_analysis.json by hand — no LLM calls
uv run livecaster export sessions/2026-08-31_demo
```

Read `TODO link:` lines in `show_notes.md` before publishing. The model is told to return `null`
rather than guess a URL, so those are honest gaps, not errors.

## Tuning between episodes

Everything lives in `llm.jsonl` in the session directory: every request, response, latency, token
count and cost. To iterate on thresholds without paying for calls again, replay the session
against its own recorded transcript:

```bash
uv run livecaster replay --session sessions/2026-08-31_demo \
    --outline osnova.md --mock-llm --speed 0 \
    --set llm.cover_threshold=0.8
```

Knobs worth touching, in order of usefulness:

- `llm.cover_threshold` — raise it if items get struck through too eagerly.
- `llm.tick_interval_s` — lower it for a fast-moving conversation, at proportional cost.
- `llm.transcript_window_words` — raise it if the model keeps losing the thread.
- `llm.reasoning_effort_tick` — `none` if ticks are slower than about ten seconds.

## Recording the STT self-test samples

`check` uses `tests/fixtures/speech_sk_30s.wav` when it exists. Record it once:

```bash
ffmpeg -f avfoundation -i ":0" -t 30 -ar 16000 -ac 1 tests/fixtures/speech_sk_30s.wav
ffmpeg -f avfoundation -i ":0" -t 30 -ar 16000 -ac 1 tests/fixtures/speech_en_30s.wav
```

Read a paragraph of your outline out loud. Then `uv run livecaster check` prints the transcript
and the real-time factor, which is the number to watch when you change engines or machines.
