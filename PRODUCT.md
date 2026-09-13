# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

The primary user is a podcast host who is **mid-conversation** while the software runs. They are
talking to a guest, in a room or over Zencastr/Meet/Riverside, and the screen is a glance away, not
a thing they read. They cannot pause to hunt for information, and they have both hands and most of
their attention on the interview.

Secondary situations, same person:

- **Before the episode**: at the desk, unhurried. Writing the outline in Markdown, running
  `livecaster check`, resolving a microphone, picking models. Reading is fine here.
- **After the episode**: at the desk again, moving the generated notes into WordPress, a social
  media manager, or a podcast host's episode form.

The audience is technically comfortable: a terminal, `uv`, a config file in TOML and an API key are
all acceptable. Hosts working in **English, Slovak and Czech are first-class**; the other languages
of the default speech engine work but are not the target.

## Product Purpose

Livecaster is a local co-pilot for a live podcast recording. It transcribes the conversation on the
host's own machine and, every ~25 seconds, asks a fast LLM to reason over the host's Markdown
outline against what was just said. The outline becomes a **live map** rather than a script: covered
topics strike through, newly relevant ones light up with a reason and a suggested segue, follow-ups
and things-to-link collect on the side. At Finish, a slower model writes the show notes in the
language of the podcast.

Success is that the host finishes an episode having covered what mattered, without having read the
screen — and walks away with notes they can paste somewhere rather than write.

Livecaster is a **public artifact, not a campaign**. It is built for its author and hosts like him,
and is open on GitHub so it can be found and evaluated. Growth is not a goal; nothing should be
designed to chase adoption or manufacture enthusiasm.

## Positioning

Three things together, which a neighbouring tool would have to give up something to copy:

1. **Local transcription, remote reasoning.** Audio never leaves the machine; only outline text,
   transcript text and derived state cross the network. This is a hard boundary, not a setting.
2. **A map, not a teleprompter.** Non-linear by design. It never autoscrolls, never advances, never
   tells the host what to say next. It reports what became relevant and the host decides.
3. **The outline file is never written to.** The host's own `.md` is opened read-only; every
   annotation lands in a session directory of its own.

It is deliberately cheap: a two-hour episode costs about $0.50 in LLM calls, measured on real
sessions, not estimated.

## Operating Context

- **The live scene is the design constraint.** The host is speaking while the screen changes. The
  live surfaces (outline map, Now panel, top bar) must carry short, highlighted, findable
  information — nothing that needs to be read in full, nothing that rewards study. The Now panel is
  already one line plus three labels of at most five words each; that is the scale, not an accident.
  This does **not** apply to Settings (opened before the episode, at rest) or to the show notes
  (read afterwards, and allowed to be verbose).
- **Three run modes**: in person (one shared microphone, or one per person), remote (host mic plus
  the browser's audio output, via AudioTee or BlackHole on macOS, PipeWire monitor on Linux), and
  replay from a file for development.
- **Two screens, one product**: the app UI is a local page served at `127.0.0.1:8766` and opened on
  the host's laptop or a tablet on the same network; the project site at `../livecaster-site` is a
  single static page where a host decides whether to install it at all. They share one visual world —
  the score: Libre Franklin for structure, Spectral italic for the cue voice, the conductor's pencil
  for what matters now — and differ in composition, because the app is glanced at from two metres
  mid-interview and the site is read at reading distance by someone deciding. `DESIGN.md` records the
  app's system; the site is a Persuade surface laid out as a critical edition, every claim carrying
  its measurement or its caveat beside it.
- **Session directory as the record.** Everything lands in `sessions/<date>_<slug>/` as it happens:
  transcript, events, WAV backups, LLM traffic, state. A crash costs the utterance in flight.
- **The sync mark.** The real recorder (Zencastr, the host's usual rig) starts at a different moment;
  the host presses `m` when it does, and chapter timestamps are relative to that.
- **Keyboard first while live**: `j`/`k`, `c`, `x`, `p`, `1`/`2`/`3`, `m`, `t`, `d`, `space`, `,`, `?`.

## Capabilities and Constraints

**Confirmed capabilities**: outline parsing from arbitrary Markdown; live local STT (Parakeet by
default, Whisper variants when a language must be forced); the tick loop and its fast lane; per-item
states (untouched, warm, touched, hot, covered, skipped, pinned) with host override; side panel of
Now / Questions / Mentions / Transcript / New topics / Result; pre-flight pass; mid-session
reconfiguration of language, tick tuning, audio channels, models and outline; pause/resume; resume
from a session directory; wrap-up producing show notes, annotated outline, transcript Markdown and
SRT, and the raw analysis JSON; `check`, `devices`, `replay`, `wrapup`, `export`.

**Constraints that bind future work**:

- **Show notes must be copyable by section, on click.** The end-of-session action is: click a whole
  section — the notes, a social post, the mentions list — and paste it into WordPress or a social
  media manager. Today only whole-file copy exists (`app.js`, the Result panel `⧉ Copy` chip); this
  is a known gap, not the intended end state.
- **The site stays one hand-editable file.** `../livecaster-site/index.html` is a single nsite-clay
  document published over Nostr: no build step, no bundler, no framework. It is edited in the browser
  with `#edit` by the owner key. Any change has to survive that.
- **Not a PWA, and no service worker** (FR-27). Version freshness is handled by a build ID embedded
  in `index.html` and echoed in the WebSocket `hello`; a mismatch reloads the client, and static
  files are served `Cache-Control: no-store`.
- **No cloud speech recognition, no accounts, no multi-user, no remote access beyond the LAN.**
- **No PyTorch on the default macOS install.** MLX and onnxruntime only; Whisper is an opt-in extra.
- **Vanilla front end.** The app UI is hand-written HTML, CSS and JS served by FastAPI, with
  `marked.min.js` vendored. There is no build pipeline on either surface.
- **Python 3.12 pinned, `uv` only.**

**Undecided / open**:

- No licence file exists in the repository yet.
- An English demo recording is planned; only the Slovak one exists.

## Brand Commitments

- The name is **Livecaster**. The site's line is "local transcription, remote reasoning".
- **Voice: flat, measured, specific.** "About fifty cents." "It says so instead of inventing a URL."
  Numbers come from measurements and are stated as such. No hype, no superlatives, no urgency.
- **No invented proof, ever.** No testimonials, customer logos, user counts, awards or benchmarks
  that do not exist. If a claim cannot be sourced from a real session or a file in the repository, it
  does not go on a surface.
- The repository is `github.com/jooray/livecaster-llm`; the site is published on nsite over Nostr.

## Evidence on Hand

Real, usable material:

- **Demo video**: <https://youtu.be/rDJfGJWyTkM>, recorded with Livecaster running on itself
  (Slovak). Thumbnail at `../livecaster-site/demo-thumb.jpg`.
- **Demo outlines**: `demo/demo-sk.md` and its English translation `demo/demo.md`.
- **Real session data**: `sessions/2026-09-03_demo-sk/` (transcript, events, LLM traffic, state),
  plus transcript fixtures in `tests/fixtures/` for sk, cs and en, and canned tick responses.
- **Measured costs**, from real sessions: a tick averages $0.0009; a Sonnet 5 wrap-up measured
  $0.14–0.20; a whole six-minute live session cost $0.17; a two-hour episode ≈ $0.50. Measurements
  and their reasoning are in `DECISIONS.md` (D8, D17).
- **Measured STT figures**: Parakeet RTF 0.055 (~0.25 s/utterance), whisper-mlx 0.62 (2.6 s),
  faster-whisper 3.68 (15 s).
- **Documents**: `SPEC.md` (FR-01…FR-39), `IMPLEMENTATION_PLAN.md`, `DECISIONS.md`,
  `docs/RUNBOOK.md`, `llms.txt`, `README.md`.

Absences future work must not paper over: **no product screenshots** in either repository, no
testimonials, no users beyond the author, no third-party press, no English demo, no licence.

## Product Principles

1. **The host is talking.** Anything on a live surface earns its place by being readable at a glance
   and ignorable without cost. Length is a defect there and only there.
2. **Suggest, never steer.** No autoscroll, no next-line, no advancing on the host's behalf. Every
   state the model proposes can be overridden by a click or a key.
3. **Local stays local.** The privacy boundary is a fact about the product, not a feature to sell.
   State it plainly once; do not decorate it.
4. **Say the measured number.** Prices, latencies and limits are quoted from real sessions, with the
   failure modes named next to them.
5. **The host's files are theirs.** The outline is read, never written. Output lands in a session
   directory and is designed to be lifted out of the app whole.

## Accessibility & Inclusion

- **FR-26 is binding**: minimum 18 px body text in the app UI, high contrast, dark theme by default
  with a light toggle, legible from 1–2 m across a desk or on a tablet. Works down to tablet width
  (≥ 768 px), where the side panel collapses into tabs.
- Every live action has a keyboard equivalent; the map is operable without a pointer.
- Interface language is English; **content** (show notes, chapters, titles, social posts) comes back
  in the language of the podcast, with English, Slovak and Czech first-class.
