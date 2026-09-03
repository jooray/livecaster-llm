# Test fixtures

## Audio

| File | Source |
|---|---|
| `speech_sk_30s.wav` | 30 s of Slovak conversation, 16 kHz mono, cut from episode 105 of *Podcast o všeličom* — "Lunarpunk, saunové rituály a prečo nechceme zmeniť svet" with Elenou (2026-08-26). Taken from `21.7 s` of a 90 s excerpt starting 5:00 into the episode, so it starts on an utterance boundary and contains no intro music. Two speakers, no music, ordinary conversational pace. |

Recreate it with:

```bash
ffmpeg -v error -y -ss 300 -t 90 \
  -i https://example.com/assets/podcast/episode-105.mp3 \
  -ar 16000 -ac 1 /tmp/ep105_90s.wav
ffmpeg -v error -y -ss 21.7 -t 30 -i /tmp/ep105_90s.wav \
  -ar 16000 -ac 1 -c:a pcm_s16le tests/fixtures/speech_sk_30s.wav
```

The feed is <https://example.com/feed/podcast/>. `speech_en_30s.wav` and `speech_cs_30s.wav`
are not committed yet; `livecaster check` falls back to a silence self-test when a sample is
missing and prints how to record one.

## Transcripts

`transcript_sk.jsonl` and friends are hand-written, not machine transcribed. They walk through
sections 0, 1 and 3 of `osnova.md` out of order and mention Wim Hof, Buteyko, ayahuasca and
Kryptocamp, plus one on-air promise, so the reducer and the exports have something realistic to
chew on. `transcript_sk_singlemic.jsonl` is the same conversation with the speaker labels
removed, for the single-microphone mode.

## LLM responses

`tick_responses/*.json` are canned tick answers served in filename order by `--mock-llm`,
including a deliberately invalid one and one with node IDs that do not exist.
`preflight.json` and `final_analysis.json` back the other two call kinds.
