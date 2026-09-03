# Decisions

Running log of implementation decisions. Newest last.

## D1 — Build backend: hatchling with a `src/` layout (M0)

`pyproject.toml` uses `hatchling` and declares `packages = ["src/livecaster"]`. Nothing in the
spec required a specific backend; hatchling is what `uv init --lib` produces and needs no plugins.

## D2 — CLI framework: typer with an explicit `main()` entry point (M0)

`project.scripts` points at `livecaster.cli:main` rather than at the typer `app` object, so the
entry point stays stable if the app object is restructured.

## D3 — Config overrides: three layers (M0)

Defaults <- `livecaster.toml` <- `LIVECASTER_*` environment variables <- `--set key=value`.
Environment keys use `__` for nesting (`LIVECASTER_LLM__TICK_MODEL`). The spec only required the
file and `--set`; the environment layer costs nothing and helps containers.

## D4 — Pricing refresh is best-effort (M0)

`llm/pricing.py` ships the static table from SPEC §11 and tries to refresh it from `GET /models`
at startup. Venice has moved the pricing key names before, so the parser accepts several spellings
and silently keeps the static table when it recognizes nothing.

## D5 — The ANSI escape in `osnova.md` is stripped, not kept (M1)

SPEC §7.1 says to strip ANSI CSI sequences, and the same section's fixture note says the
`[118;1:3u` characters in section 6 "remain harmless text". The file contains a real
`ESC [118;1:3u` (a kitty keyboard-protocol echo), so the two statements cannot both hold.
The parser rule wins: the sequence is removed from the node's `text`. The annotated export
still reproduces the source byte for byte, because it renders from the original file, not
from the parsed text — so nothing is lost from the user's outline.

## D6 — Fast lane scores triggers and node words separately (M3)

SPEC §7.6 defines the fast-lane score as "the fraction of a node's phrases found", warming at
≥ 0.5. Taken literally over triggers *plus* every significant word of the node, long outline
items (T29 has 40 words) can never reach 0.5, and the spec's own worked example — a guest says
"ayahuasca" and `Psychedeliká vs. dych` lights up — would never fire.

`session/fastlane.py` therefore scores the two groups separately: a pre-flight trigger is a
phrase the model picked *because* it points at that one node, so one hit warms the node
(score `max(0.5, hits/len(triggers))`); the node's own words are generic, so they keep the
spec's half-the-phrases rule. Matching is diacritics-insensitive; single words match whole
words or a ≥ 85 `rapidfuzz.ratio` against a word (not a substring of the text), so "dychom"
does not match "dych" while "psychedelikách" does match "psychedeliká".

## D7 — `mlx-whisper` moved out of the default macOS extra (M0)

`mlx-whisper` depends on PyTorch, and ground rule 2 of the implementation plan forbids torch on
macOS. `uv sync --extra mac` therefore installs only `parakeet-mlx`; the Whisper fallback lives
in a separate `mac-whisper` extra that a user installs deliberately when they need a language
outside Parakeet's 25. The `whisper-mlx` engine code is unchanged and imports lazily, so it
simply reports that the package is missing until the extra is installed.

## D8 — Tick model and reasoning effort, decided by measurement (M2)

SPEC §10 fixes `tick_model = "deepseek-v4-flash-0731"` and `reasoning_effort_tick = "low"`, and
§5 requires a tick p95 of ≤ 10 s. Measured against Venice on 2026-09-03 with the real Slovak
fixture (3.5k prompt tokens, mid-episode state, ten calls):

| Configuration | Median | p95 | Result |
|---|---|---|---|
| `deepseek-v4-flash-0731`, effort `low` | — | — | **Unusable.** All 1500 completion tokens go to reasoning, `finish_reason: length`, empty content. |
| `deepseek-v4-flash-0731`, effort `none` | 38 s | 45 s | Correct JSON, ~25 tokens/s — four times over the latency budget with a 25 s tick interval. |
| `deepseek-v4-flash-0731-fast`, effort `none` | **8.9 s** | **10.1 s** | Correct JSON, ~110 tokens/s. |

Three changes follow, all of them mitigations SPEC §5 and §12 already name:

1. `reasoning_effort_tick` defaults to `none`. This is the fallback SPEC §5 prescribes when the
   measured p95 exceeds 10 s, and on this model `low` returns nothing at all.
2. `tick_model` defaults to `deepseek-v4-flash-0731-fast` — the "`-fast` model option" from the
   §12 risk table. `final_model` stays on plain flash: the wrap-up is one call where latency does
   not matter and the cheaper rate does.
3. `max_tick_tokens` 1500 → 2500 (real answers reached 1724 and were being truncated) and
   `tick_timeout_s` 30 → 45.

Cost with these defaults, for a two-hour episode: 290 ticks × (170 fresh + 3300 cached prompt
tokens, ~950 completion) ≈ **$0.29**, plus ~$0.01 for the wrap-up. Comfortably inside the $1
target of SPEC §5, and cheaper than the spec's own estimate because the real prompt is 3.5k
tokens rather than 8k.

## D9 — `reasoning_effort: "none"` is sent, never omitted (M2)

Leaving the field out is not the same as `none` on this model: an omitted field produced an 8635
token completion on one call and 1565 on the next. `llm/client.py` therefore always sends
`reasoning_effort`, including the literal `"none"`.

## D10 — Ticks report only what changed (M2)

The tick prompt gained rule 3b: never repeat an item that STATE already lists as covered, touched
or skipped, nor a mention already in MENTIONS SO FAR. Mid-episode `covered` arrays dropped from
seven entries to two, which cuts both latency and output cost. The reducer already ignored
re-covers, so nothing about the state machine changed.

## D11 — Venice prompt caching confirmed working (M2)

With a byte-identical system message and a stable `prompt_cache_key`, the second and later calls
report `prompt_tokens_details.cached_tokens = 3328` out of 3470 prompt tokens — a 96 % hit rate.
`tests/integration/test_venice_smoke.py::test_identical_prefix_hits_the_prompt_cache` asserts it
outright rather than as an expected failure.

## D12 — The wrap-up needs a 32k token budget and a 10-minute timeout (M6)

SPEC §7.7 asks for the final analysis at `reasoning_effort: high` with `max_completion_tokens`
8000 and a 120 s timeout. Measured on the Slovak fixture (5.2k prompt tokens):

| Configuration | Time | Tokens | Result |
|---|---|---|---|
| flash, `high`, max 8000 | — | — | **Empty content**: reasoning alone exceeds the budget. |
| flash, `high`, max 32000 | 425 s | 14.2k reasoning + 6.8k output | Complete, best titles. |
| flash, `none`, max 16000 | 116 s | 5.5k output | Complete, slightly plainer. |
| flash-fast, `none`, max 16000 | 32 s | 4.9k output | Complete, but produced a malformed Slovak word in a title. |

`reasoning_effort_final` stays `high` as the spec says — this is a single call at the end of a
two-hour recording and the output is what the host publishes. The budget grows to 32000 tokens and
`final_timeout_s` to 600, because the spec's own numbers cannot produce any output at all.
`livecaster wrapup --set llm.reasoning_effort_final=none` finishes in about two minutes if the
seven-minute wait is not worth it. The extra reasoning tokens cost under a cent.

## D13 — A heading only covers its leaves when it has no sub-headings (M2)

SPEC §9.3 rule 3 lets a `covered` entry for a heading strike every coverable leaf underneath it at
confidence ≥ 0.85. In a real Venice run the model returned `T1` (`# Osnova podcastu s Zuzkou…`,
the document title, which owns all eight sections) at 0.9, and the outline went from 16/41 to
41/41 covered in one tick — including a whole section the conversation never reached.

`session/reducer.py` now expands a heading only when none of its descendants is itself a heading.
"This section of bullets is done" stays expressible; "the whole episode is done" does not.

## D14 — Tick timeout raised to 60 s (M2)

`deepseek-v4-flash-0731-fast` answers in ~9 s median, but Venice occasionally queues a request far
past that: a full replay at `--speed 0`, which fires ticks back to back with no wall-clock spacing,
lost five of seventeen ticks to a 45 s `ReadTimeout`. Ticks never overlap and a skipped tick is
only a missed update, but the tokens are paid for either way, so the timeout is now 60 s. A live
session ticks once per 25 s and does not push the endpoint nearly this hard.
