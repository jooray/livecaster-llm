# Test fixtures

## Audio

No speech sample is committed. `livecaster check` runs a silence self-test when one is missing and
prints how to record your own:

```bash
ffmpeg -f avfoundation -i ":0" -t 30 -ar 16000 -ac 1 tests/fixtures/speech_sk_30s.wav
```

Thirty seconds of ordinary conversation in the language you record in, 16 kHz mono, starting on an
utterance boundary and with no intro music. `check` picks it up automatically and prints the
transcript and the real-time factor.

## Transcripts

`transcript_sk.jsonl` and friends are hand-written, not machine transcribed. They walk through
sections 0, 1 and 3 of `fixtures/osnova.md` out of order and mention Wim Hof, Buteyko, ayahuasca and
Kryptocamp, plus one on-air promise, so the reducer and the exports have something realistic to
chew on. `transcript_sk_singlemic.jsonl` is the same conversation with the speaker labels
removed, for the single-microphone mode.

## LLM responses

`tick_responses/*.json` are canned tick answers served in filename order by `--mock-llm`,
including a deliberately invalid one and one with node IDs that do not exist.
`preflight.json` and `final_analysis.json` back the other two call kinds.
