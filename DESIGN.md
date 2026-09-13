---
name: Livecaster
description: The plan as a conductor's score you can restart anywhere.
colors:
  paper: "#0e0f11"
  paper-up: "#16181b"
  ink: "#f2efe6"
  ink-quiet: "#9498a1"
  staff: "#4c5058"
  rest: "#7d838d"
  pencil: "#e4573a"
  mark: "#79a8e8"
  ok: "#4ec98a"
  danger: "#ff6b6b"
  sel: "rgba(242, 239, 230, .07)"
  paper-print: "#f4f1e8"
  paper-up-print: "#fffdf7"
  ink-print: "#16171a"
  ink-quiet-print: "#585d66"
  staff-print: "#bab5a6"
  rest-print: "#6f747c"
  pencil-print: "#c0331a"
  mark-print: "#1f55b8"
  ok-print: "#17864f"
  danger-print: "#c22b2b"
  sel-print: "rgba(22, 23, 26, .055)"
typography:
  display:
    fontFamily: "Libre Franklin, -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif"
    fontSize: "clamp(30px, 3.4vw, 42px)"
    fontWeight: 700
    lineHeight: 1.05
    letterSpacing: "-.02em"
  headline:
    fontFamily: "{typography.display.fontFamily}"
    fontSize: "32px"
    fontWeight: 600
    lineHeight: 1.1
    letterSpacing: "-.02em"
  title:
    fontFamily: "{typography.display.fontFamily}"
    fontSize: "21px"
    fontWeight: 600
    lineHeight: 1.45
    letterSpacing: "-.01em"
  body:
    fontFamily: "{typography.display.fontFamily}"
    fontSize: "18px"
    fontWeight: 400
    lineHeight: 1.45
    letterSpacing: "normal"
  cue:
    fontFamily: "Spectral, Georgia, Times New Roman, serif"
    fontSize: "19px"
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "normal"
    fontStyle: "italic"
  rehearsal-mark:
    fontFamily: "{typography.display.fontFamily}"
    fontSize: "22px"
    fontWeight: 700
    lineHeight: 1
    letterSpacing: ".02em"
  numeric:
    fontFamily: "{typography.display.fontFamily}"
    fontSize: "18px"
    fontWeight: 400
    lineHeight: 1.45
    fontFeature: "tnum 1"
rounded:
  none: "0"
  sm: "3px"
  md: "4px"
  pill: "99px"
spacing:
  hair: "4px"
  tight: "8px"
  line: "13px"
  gutter: "20px"
  system: "26px"
  edge: "clamp(14px, 2.4vw, 30px)"
  margin-w: "62px"
  margin-w-narrow: "44px"
components:
  rehearsal-mark:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    typography: "{typography.rehearsal-mark}"
    rounded: "{rounded.none}"
    padding: "0 7px"
    height: "38px"
  rehearsal-mark-live:
    textColor: "{colors.pencil}"
  rehearsal-mark-rest:
    textColor: "{colors.rest}"
    height: "32px"
  stave:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.sm}"
    padding: "4px 8px 4px 6px"
  stave-hover:
    backgroundColor: "{colors.sel}"
  stave-selected:
    backgroundColor: "{colors.sel}"
  stave-cut:
    textColor: "{colors.ink-quiet}"
  stave-accent:
    textColor: "{colors.mark}"
  play-line:
    backgroundColor: "transparent"
    textColor: "{colors.pencil}"
    typography: "{typography.display}"
    width: "24ch"
  play-cue:
    textColor: "{colors.ink}"
    typography: "{typography.cue}"
    padding: "0 0 0 15px"
    width: "54ch"
  rank-badge:
    backgroundColor: "{colors.pencil}"
    textColor: "{colors.paper}"
    rounded: "{rounded.sm}"
    padding: "1px 9px"
  button:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    padding: "4px 12px"
  button-primary:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.paper}"
    rounded: "{rounded.sm}"
    padding: "4px 12px"
  button-danger:
    backgroundColor: "transparent"
    textColor: "{colors.pencil}"
    rounded: "{rounded.sm}"
    padding: "4px 12px"
  chip:
    backgroundColor: "transparent"
    textColor: "{colors.ink-quiet}"
    rounded: "{rounded.pill}"
    padding: "3px 13px"
  chip-active:
    textColor: "{colors.ink}"
  input:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    padding: "5px 8px"
  dialog:
    backgroundColor: "{colors.paper-up}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "22px clamp(18px, 3vw, 30px)"
    width: "min(760px, 94vw)"
  overlay:
    backgroundColor: "{colors.paper-up}"
    textColor: "{colors.ink}"
    rounded: "{rounded.none}"
    padding: "14px 18px 30px"
    width: "min(520px, 100%)"
  toast:
    backgroundColor: "{colors.paper-up}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    padding: "10px 14px"
---

# Design System: Livecaster

## Overview

**Creative North Star: "The Conductor's Score"**

The app is an outline drawn the way an orchestra's score is drawn. An orchestra can re-enter at any
bar because the score carries lettered rehearsal marks in the margin; a podcast host has the same
problem when a guest jumps three topics ahead. So the margin letters *are* the jump keys — the mark
shows a capital, you press that capital. Sections are **systems**: a mark in the margin, a hairline
bracket gathering the staves, the section name and its fraction, then the lines. A covered line takes
a single engraver's cut. A section entirely behind you rests to one line. The one topic worth playing
now is set at display scale in conductor's pencil red with a cue note beneath it in italic serif —
the small line a score prints to show a player how to come back in.

Dark is the default because the room is dim and the reader is one to two metres away. Light is not
the dark palette inverted; it is the score **as printed**: paper ground, black ink, the same pencil,
one hue-shift darker so the red still reads on cream. Both themes carry the same token names, so
nothing in a component knows which theme it is in.

The system refuses the dark operator dashboard where forty rows compete at equal weight and hierarchy
is carried by colour alone. Weight, size, rule and rest do the hierarchy here; colour is a second
channel, never the only one. The screen is consulted mid-sentence and then abandoned, so every state
has to be legible in a glance of under a second and cost nothing to ignore.

**Key Characteristics:**
- Notation, not chrome: brackets, cuts, accents, rests and rehearsal marks are the vocabulary.
- Flat and ruled — hairlines and tonal lift only, no shadows anywhere.
- Two typefaces with fixed jobs: Libre Franklin carries structure, Spectral italic only ever speaks a cue.
- Dark by default, printed-score light, one token set for both.
- Keyboard is the control surface; the margin letter is the primary affordance.
- 18px body floor (FR-26), self-hosted faces, no runtime fetches, no service worker (FR-27).

## Colors

A bone-on-near-black score with exactly two signal hues: the conductor's pencil and the host's own
mark. Everything else is ink, quiet ink, staff grey and rest grey.

### Primary
- **Conductor's Pencil** (`{colors.pencil}` dark / `{colors.pencil-print}` light): the mark a
  conductor makes on a score. It means *this is the passage to play now* — the live system's
  rehearsal mark and bracket, the live passage line and its rank badge, the cue note's rule, the hot
  line, the playhead underline, the recording clock, the over-target budget figure, the drop target,
  the edge counters, and the focus ring and selection highlight. It is never decoration and never a
  brand colour: if it is on screen, something is being asked of the host.
- **Mark Blue** (`{colors.mark}` / `{colors.mark-print}`): the *host's* mark, not the model's — a
  line bolded in the outline and still unplayed (the marcato accent), the count of open must-asks,
  the sync badge, links, and the first speaker in the transcript.

### Neutral
- **Bone / Print Paper** (`{colors.ink}` on `{colors.paper}`; inverted in light to
  `{colors.ink-print}` on `{colors.paper-print}`): the page and its ink. Bone rather than white
  because pure white at 2 m in a dim room glares.
- **Lifted Paper** (`{colors.paper-up}` / `{colors.paper-up-print}`): dialogs, the overlay, toasts
  and preformatted blocks lift off the page by one tonal step.
- **Quiet Ink** (`{colors.ink-quiet}` / `{colors.ink-quiet-print}`): timestamps, fractions,
  sub-headings, labels, status pills, secondary prose — read only when looked for.
- **Staff Grey** (`{colors.staff}` / `{colors.staff-print}`): every hairline. Rules, brackets,
  borders, scrollbar thumbs, the cut's strike line.
- **Rest Grey** (`{colors.rest}` / `{colors.rest-print}`): a system or line that is behind you.
  Distinct from quiet ink: quiet means secondary, rest means finished.
- **Selection Wash** (`{colors.sel}` / `{colors.sel-print}`): a 5–7% ink wash for hover and the
  selected stave. It reads as a hand resting on the page, not as a highlight.

### Tertiary
- **Confirmed Green** (`{colors.ok}` / `{colors.ok-print}`): pinned check, a copied section, the
  second transcript speaker, the success toast border. Never a status of its own.
- **Failure Red** (`{colors.danger}` / `{colors.danger-print}`): destructive affordances and error
  toast borders only. It is deliberately a different red from the pencil; a failing service must
  never look like a topic to raise.

### Named Rules
**The Two Marks Rule.** A screen has at most two signal hues: the pencil (the model says: now) and
the mark blue (the host said: don't miss this). Any new state finds a home in weight, rule or rest
before it asks for a third hue.

**The Same Pencil Rule.** Light theme is the score as printed, not an inversion. Paper is cream
(`{colors.paper-print}`), ink is black, and the pencil keeps its identity at a darker value so it
survives on paper. Never recolour a signal between themes; only revalue it.

**The Not-Danger Rule.** Pencil red means *do something in the conversation*. Failure red means *the
software broke*. They never swap jobs, and a failure never borrows the pencil.

## Typography

**Display / Body Font:** Libre Franklin (with `-apple-system`, `Segoe UI`, Roboto, sans-serif)
**Cue Font:** Spectral Italic (with Georgia, Times New Roman, serif)

**Character:** Libre Franklin is the engraver's hand: plain, dense, legible at distance and at small
sizes, and it carries *everything* structural — marks, section names, lines, chrome, numbers.
Spectral italic appears in exactly one place, the cue note under the live passage, the way a score
reserves italic for expression marks. That scarcity is what makes the cue note read as a different
voice — the model talking, not the outline.

### Hierarchy
- **Display** (700, `clamp(30px, 3.4vw, 42px)`, 1.05, balanced wrap, max 24ch): the live passage
  line, and only that. It exists at this size because it must be readable at 2 m without being read.
- **Headline** (600, 32px, 1.1): the elapsed clock. Also 26px for the wrap-up heading and the
  empty-score heading, 24px for the episode title and dialog headings.
- **Title** (600, 21px): a system's name. Drops to 19px below 560px, and to 18px at weight 400 in
  rest grey when the system rests.
- **Body** (400, 18px, 1.45, max 76ch): every stave line. This is the floor, not a default —
  FR-26 binds it.
- **Cue** (Spectral italic 400, 19px, 1.5, max 54ch): the reason line in ink and the segue in quiet
  ink, behind a 1px pencil rule.
- **Label** (400–700, 15–17px): timestamps, fractions, status pills, chips, rank badges, buttons,
  `kbd`. See the drift note below.

### Named Rules
**The Reserved Italic Rule.** Spectral italic is the cue note's voice and nothing else's. No italic
serif on headings, empty states, toasts, or anywhere the outline's own words appear.

**The Self-Hosted Rule.** Both faces ship as woff2 in `src/livecaster/ui/fonts/`, split latin and
latin-ext so Slovak and Czech diacritics survive, and are served from `/static/fonts/`. A network
failure mid-episode is a designed-for state; the typography must not change when it happens. Never
add a Google Fonts link, an `@import`, or any runtime font fetch.

**The Capitals Rule.** Rehearsal marks are always upper case (`A`…`Z`, then `AA`) because the jump
key is literally the capital shown. Lower-case keys keep their existing meanings (`j`/`k`, `c`, `x`,
`p`, `m`, `t`, `q`, `l`, `n`, `w`, `d`), so a new key must never be lower case if it would collide.

**The Tabular Rule.** Anything that counts or ticks — clock, sync badge, fractions, timestamps, cost,
rank, the budget line — carries `font-variant-numeric: tabular-nums`, so a figure changing does not
shift the line beside it.

**The 18px Floor.** Body text is 18px and never smaller. Sizes below 18px are labels only, and new
content type does not join them.

*Carried drift, not a rule:* the head chrome, status pills, dialog tables and chips run 15–17px
today. That sits under FR-26's floor for anything the host might actually need to read live. It is
recorded here as a defect the build carries; do not extend it to new surfaces and do not treat those
sizes as a sanctioned label ramp for content.

## Layout

The page is a fixed head over a single scrolling score; the body never scrolls.

- **Head** (`flex: none`, 12px vertical, `clamp(14px, 2.4vw, 30px)` horizontal, 1px staff rule
  below): clock, target, budget, meters, status pills, and the controls pushed right with
  `margin-left: auto`. It wraps rather than truncating.
- **Score** (`flex: 1`, scrolls in y only, same horizontal inset, **40vh of bottom padding**) so the
  last system can still sit mid-screen. Systems have a `max-width: 1180px`; stave text caps at 76ch.
- **The system grid**: two columns, `62px` margin plus `minmax(0, 1fr)` content, 20px gutter, 26px
  between systems (14px when rested). The bracket is a 1px absolute rule in the margin from 44px
  down to the system's foot.
- **Rhythm**: 4 / 8 / 13 / 20 / 26px. Staves are 4px tall in padding with a −6px left bleed so the
  hover wash extends past the text edge.
- **Overlay**: an inset-right panel at `min(520px, 100%)` with a 1px staff border-left. It is never a
  tab strip and never steals width from the score permanently.
- **Wrap-up**: a full-cover screen (`inset: 0`) on the base paper, content capped at 74ch.

### Responsive
- **≤ 860px** (tablet, or a window sharing the screen with a video call): the margin narrows to 44px,
  the gutter to 13px, **the bracket disappears** (the mark alone carries the system), rehearsal marks
  shrink to 32px, timestamps lose their fixed 5.5ch column, the overlay goes full width and the
  controls stop being right-aligned.
- **≤ 560px**: clock 26px, system name 19px, the live passage line 28px.
- The score **never scrolls itself**. When hot lines are out of view, a pencil-ruled edge counter
  appears at the top or bottom of the pane and waits to be clicked.

### Named Rules
**The No-Autoscroll Rule.** Nothing moves the viewport unless the host asked: a jump key, a mark
click, or an edge counter. `buildScore()` restores `scrollTop` explicitly after every rebuild.

**The Document Order Rule.** Systems render in the outline's order, always. Nothing reorders,
promotes or filters the host's document — rest and cut are the only compressions.

## Elevation & Depth

Flat. There are no shadows in this system and none should be added. Depth is carried three ways: a
single tonal step up to `{colors.paper-up}` for dialogs, the overlay, toasts and code blocks; 1px
staff-grey hairlines for every border and rule; and stacking order for the three planes (score → edge
counters and overlay → wrap-up screen → toasts). The dialog backdrop is a 58% black wash.

The three `box-shadow` declarations in the build are not elevation and must not be read as licence
for shadows: `inset 2px 0 0 ink` is the selected stave's left rule, `0 1px 0 pencil` is the
playhead's underline, and the scrollbar thumb's border is a spacing trick.

### Named Rules
**The Engraved Flat Rule.** A score is printed, not lit. No drop shadows, no glows, no gradients, no
blur. If something must separate from the page, it takes a hairline or a tonal step.

## Shapes

Almost nothing is round. Radii are effectively two values: 3px on interactive rectangles (buttons,
inputs, staves, rank badges, toasts, `kbd`), 4px on the largest containers (dialogs, the drop target,
copyable wrap-up sections) and the pill (99px) reserved for chips and scrollbar thumbs. The rehearsal
mark is **square on purpose** — a 2px box with no radius, min 38px wide, 38px tall, because that is
how a score prints a rehearsal letter. The bracket, the rules and the cut are 1px lines. Borders
carry meaning by colour: staff grey is structure, ink is emphasis, pencil is live, danger is
destructive.

### Named Rules
**The Boxed Mark Rule.** The rehearsal mark never gains a radius, a fill, or an icon. Live turns its
border and letter pencil red; rest thins the border to 1px, shrinks the box to 32px and drops it to
rest grey.

## Components

### Rehearsal Mark
The signature component. A square outlined button in the margin carrying a capital letter, which is
also the key that jumps there.
- **Shape:** 2px ink border, no radius, min-width 38px, height 38px, 22px/700 letter.
- **Hover:** border and letter turn pencil.
- **Live:** pencil border and letter; the bracket below it turns pencil too.
- **Rest:** 1px rest-grey border, 32px box, 19px/500 letter, and the bracket is removed.
- **Behavior:** click or press the capital; the view scrolls to the system's top and selects its
  first line. Always carries an `aria-label` naming the section.

### System (section)
- **Anatomy:** margin (mark + bracket) | content (name + fraction, then staves).
- **Name:** 21px/600, with a quiet-ink fraction beside it that reads `3/7` while lines remain, and
  `all 7 · 12:04–18:31` once none do.
- **Rested:** one line, mark shrunk, bracket gone, name in rest grey at body weight.
- **Behavior:** clicking the name toggles the rest open again. **A rested system must stay openable**
  — a mark made inside it can only be undone if its lines can be brought back.

### Stave (a line)
A clickable row of timestamp | accent glyph | text | rank.
- **Shape:** 3px radius, −6px left bleed, `scroll-margin: 90px`, 5.5ch timestamp column.
- **Hover / Selected:** selection wash; selected adds an inset 2px ink rule on the left.
- **State vocabulary** (classes are applied and cleared together on every patch):
  - `warm` — mentioned, unconfirmed: 1px dotted rest-grey underline.
  - `current` — the model's playhead: 1px pencil underline.
  - `hot` — relevant now but not the top-ranked: text in pencil, plus a filled pencil rank badge for ranks 1–3.
  - `accent` — a line the host bolded and has not played: mark blue, with a `∧` marcato in the gutter.
  - `cut` — covered: quiet ink, struck through in staff grey at 1px, timestamp shown.
  - `skipped` — rest grey, timestamp column reads `tacet`.
  - `pinned` — a green check appended.
  - `accent cut` — the accent surrenders: both the text and the marcato drop to quiet/rest grey.

### Live Passage
The one reachable top-ranked topic, replacing its stave in place.
- **Anatomy:** rank badge · display line · cue note (reason in ink, segue in quiet ink, both in
  Spectral italic behind a 1px pencil rule) · optional pre-flight block.
- **Motion:** `strike` — 200ms, 3px drop, `cubic-bezier(.2,.9,.2,1)`, once. It arrives struck, not
  faded: the host is mid-sentence. Disabled under `prefers-reduced-motion`.

### Buttons
- **Shape:** 3px radius, 1px staff border, transparent ground, 4–5px × 12–14px padding, 16–17px text.
- **Hover:** border goes ink. No fill change, no movement.
- **Primary:** ink ground, paper text, 600.
- **Danger:** pencil text and border; hover fills pencil with white text.
- **Link:** borderless, pencil, underlined with a 3px offset.

### Chips
Pill (99px), 1px staff border, quiet-ink text. Active takes an ink border and ink text; in the
language picker, active fills ink with paper text. Used for wrap-up file switching and language
choice only.

### Inputs
Paper ground inside a lifted dialog, 1px staff border, 3px radius, 17px text, labels stacked above in
quiet ink. Focus turns the border pencil (in addition to the global focus ring).

### Overlay
Transcript, questions, mentions and new topics are keys that overlay and leave — never a tab strip
stealing room from the plan. Right-edge panel on lifted paper, 1px staff border-left, a head with a
20px title and an `Esc` close, a body of list rows separated by 45%-opacity staff hairlines.
`aria-live="polite"`.

### Wrap-up Screen
The opposite activity: at rest, reading, everything built to be lifted out whole. Full-cover, base
paper, 74ch column, file chips across the top, and per-section blocks that wash on hover, reveal a
copy control at the top-right, and flash a 12% green tint when copied.

### Toasts
Bottom-right stack, lifted paper, 3px radius, 17px text, border colour carrying the kind: danger for
error, pencil for warn, green for success.

### Browser Surfaces
The browser is not allowed to draw in nobody's design system.
- **Selection:** pencil ground, white text.
- **Focus ring:** 2px pencil, 3px offset, 2px radius, `:focus-visible` only.
- **Scrollbars:** thin, staff-grey thumb on a transparent track, 11px wide with a 3px paper border
  giving the thumb its inset.
- **`color-scheme`** and **`accent-color: pencil`** are set per theme so native controls follow.

### Named Rules
**The Patch-In-Place Rule.** The score is rebuilt only when its *shape* changes — a system coming to
rest or being reopened, or the live passage moving to a different line (`shapeKey()` is exactly
`live id | per-system rest bits | manually-opened rests`). Every other tick recolours existing rows
through `updateStave()` and repaints fractions. Replacing the DOM under a host mid-glance is the one
thing this structure must never do. Any new state must either fold into `updateStave` or be added to
`shapeKey` deliberately.

**The Rest-Is-Reversible Rule.** Compression is never a one-way door. Every rested system's name
stays clickable and reopens it.

**The One Passage Rule.** Exactly one line is ever at display scale: rank 1 among hot items still
open. Ranks 2 and 3 stay staves with a small badge. Big is reserved, not permanent.

## Do's and Don'ts

### Do:
- **Do** give every new state a non-colour cue as well as a colour — a rule, a strike, a glyph, a
  weight, or a rest. Hierarchy carried by colour alone is the exact thing this world refuses.
- **Do** keep body text at 18px (FR-26) and the score legible at 1–2 m.
- **Do** route new state through `updateStave()` and leave `buildScore()` for genuine shape changes.
- **Do** keep rehearsal-mark keys as capitals and add new shortcuts as capitals or unused lower-case
  keys, never by reassigning `j`/`k`/`c`/`x`/`p`/`m`/`t`/`q`/`l`/`n`/`w`/`d`.
- **Do** self-host any new face as woff2 with latin + latin-ext subsets, and preload only the one the
  first paint needs.
- **Do** define both themes at once by adding the token under `:root` and `html[data-theme="light"]`;
  a component must never branch on theme.
- **Do** use tabular numerals on anything that counts or ticks.
- **Do** let the head wrap rather than truncate; a clipped budget figure is worse than a second line.

### Don't:
- **Don't** add shadows, glows, gradients or blur. Hairlines and one tonal step are the whole depth
  vocabulary.
- **Don't** fetch fonts, stylesheets or scripts from a CDN at runtime, and **don't** register a
  service worker (FR-27); freshness is the build ID echoed in the WebSocket `hello`.
- **Don't** use Spectral for anything but the cue note.
- **Don't** introduce a third signal hue, and don't reuse pencil red for software failures or danger
  red for conversational urgency.
- **Don't** give the rehearsal mark a radius, a fill or an icon.
- **Don't** autoscroll, reorder, filter or advance the host's outline. The edge counter reports; it
  does not travel.
- **Don't** let a rested system become unreachable, and don't add a compression that cannot be undone
  by clicking.
- **Don't** turn the overlay into a tab strip, or put anything decision-relevant off screen.
- **Don't** extend the sub-18px chrome sizes into content; they are a carried drift from FR-26, not a
  ramp.

## Known Gaps

Recorded so a later agent does not mistake absence for a decision.

- **First run is thin.** Beyond the dashed pencil drop target and a short empty-score block ("no
  outline loaded" plus a `choose one` link), there is no designed empty or first-run treatment — no
  onboarding, no sample outline preview, no pre-flight-empty state. Anything built here is new
  design, not an extension of an existing pattern.
- **Two navigation vocabularies coexist.** `A`–`Z` jumps to a *system* by the letter printed in the
  margin; `1`/`2`/`3` selects the model's *suggestion* by rank. They are different objects reached by
  different alphabets, they are not unified, and the rank keys have no visible affordance in the
  margin the way the letters do.
- **The tablet overlay does not collapse into tabs** the way PRODUCT.md's accessibility note
  describes; below 860px it simply takes the full width.
- **Glyph icons in the head.** `⚙`, `◐`, `?` and `⧉` are typographic glyphs standing in for icons.
  They are what shipped; they are not a sanctioned icon system, and a real icon set (inline SVG)
  should replace them rather than the glyphs being extended.

## Other Surfaces

This file describes **the app UI at `src/livecaster/ui/`** — the Operate surface a host consults
mid-interview. The project site at `../livecaster-site` is a **separate Persuade surface**: a single
hand-editable static page where a reader decides whether to install Livecaster at all. It is in the
same world and may share the palette and the faces, but it has its own composition, its own type
scale and its own components, and it is being redesigned independently. Nothing here — the system
grid, the rehearsal mark, the state vocabulary, the patch-in-place rule — is binding on it, and the
site's choices are not evidence about this system. PRODUCT.md records that the app-to-site chrome
link was deliberately **not** confirmed as binding.
