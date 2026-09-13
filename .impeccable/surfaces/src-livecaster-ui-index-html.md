---
version: 1
slug: "src-livecaster-ui-index-html"
primary_target: "src/livecaster/ui/index.html"
related_targets: []
---

# Surface brief — Livecaster app UI (`src/livecaster/ui/`)

Mode: **Operate**. The visitor is a podcast host mid-interview; the screen is consulted, never read.

## Structure (locked with the user, 2026-09-13)

Map-first, absorbing two ideas from the rejected structures:

- The plan is the whole interface. No tab strip — nothing decision-relevant can be off screen.
- The map self-compresses: a finished section rests to one line **in document order** (never reordered),
  covered items lose weight, the live item swells to display scale with reason and segue inline.
- **Bold lines in the outline are must-asks.** Existing convention, no new syntax; they escalate as
  time runs out.
- The **time budget is one line** in the head: elapsed against target, topics unasked, must-asks open.
  It informs; it never regroups the map.
- The **lede** — one dominant statement — appears only when something goes hot and retreats when
  nothing is urgent. Big is reserved, not permanent.
- Transcript, questions, mentions and promised links are **keys that overlay and leave**.
- The wrap-up is a **separate screen**, not a tab: at rest, verbose, every block click-to-copy.

Anti-goals: no autoscroll, no next-line ribbon, no reordering the host's document, nothing that must
be read while talking.

## Direction contract

**THESIS.** The plan as a score you can restart anywhere. An orchestra re-enters at any bar because
the score carries lettered rehearsal marks in the margin; the host has the same problem when a guest
jumps, so the margin letters *are* the jump keys. It refuses the dark operator dashboard where forty
items compete at equal weight and hierarchy is carried by colour alone.

**OWN-WORLD.** Bone (#f2efe6) on near-black (#0e0f11) — a score inverted for a dim room and a reader
two metres away, not paper in the hand. Conductor's pencil red (#e4573a) for the passage to play now;
a cool mark blue (#79a8e8) for lines the host marked; staff grey (#4c5058) for hairline brackets and
timestamps. Libre Franklin carries structure; Spectral italic is reserved for cue notes, the way a
score reserves italic for expression. Components: the boxed rehearsal mark, the system bracket, the
engraver's cut, the marcato accent, the cue note. Both faces self-hosted as woff2 — the app has no
build step and must not fetch fonts at runtime.

**STORY.** The host glances and learns, without reading: which system they are in, what is behind
them, which letter to press to get somewhere specific, and whether a line they marked is still
unplayed. Then they look away.

**FIRST VIEWPORT.** A head line of clock, target and budget above a single staff rule. Below it,
systems stacked in document order: a lettered mark in the 64px margin, a bracket gathering the
staves, the section name and its fraction, then the lines. Finished systems rest to one line with a
smaller, unbracketed mark. The live system's mark turns pencil red and its passage sets at
clamp(32px,3.6vw,44px) with the cue note beneath in italic serif. The primary action is a key, not a
button; the keyboard is the control surface.

**FORM.** Conductor's score with rehearsal marks — candidate 6 of seven grounded directions, assigned
by the roll and chosen by the user over the challenger that had beaten it on both axes. Seed key
`dc8ab1f1`.

**FINISH.** unreviewed and undocumented is unfinished; this build ends with the finish review, the
verdict, DESIGN.md, and every shipping raster carrying its provenance.

## Reference

Approved mock: `.impeccable/mocks/worlds/rehearsal-marks.html` (code-led; this is the compositional
reference, not a comp to match pixel for pixel). Structure studies: `.impeccable/mocks/structure/`.

## Binding constraints

FR-26 (18px body floor, high contrast, dark default with a light theme, works to tablet width);
FR-27 (build-ID reload, no service worker); vanilla JS with no build step; existing keyboard
shortcuts keep their meanings and new keys are additive; the reducer, WebSocket protocol and file
outputs are untouched.

## States to design, not discover

No outline loaded · pre-flight running · 5 vs 60 items · nothing covered vs everything · two or three
hot at once · LLM failing while transcription continues · paused · resumed after a crash · wrap-up
running · wrap-up failed. Widths: 2 m second monitor, ~900px windowed beside a call, tablet ≥768px.
