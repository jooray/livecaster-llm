# Livecaster — a live co-pilot for podcast hosts

**What this is:** a screencast walkthrough of the tool that is transcribing this very recording.
**Format:** solo, one microphone, ~15 minutes.
**Rule for myself:** show the thing running before explaining how it works.

---

## 1. The problem

- I record long-form interviews and I write an outline for every one of them
- Mid-conversation I lose track of what we already covered — and I only find out afterwards, cutting
- Two failure modes: asking something we already answered, and forgetting the one question I actually cared about
- Paper notes do not update themselves; a second screen full of text is worse than nothing

**Question to answer on camera:** why is this not just "run Whisper afterwards"?

## 2. What it does — show, do not tell

- Point the browser at the map: the outline is the interface, nothing else
- Talk about a topic, wait, watch the line strike through with a timestamp
- Show the three states: ~~covered~~, ◐ touched, ⏭ skipped
	- covered needs a verbatim quote from the transcript as evidence, not vibes
	- a passing mention is only "touched"
- Show **Now** and **Next**: one line, three labels of five words each
	- press `d` to reveal the model's reasoning and its suggested segue
	- the point is that you never read paragraphs while talking
- Press `c` to cover something the model missed — and note that it then stays out for ten minutes
- Show **Mentions**: books, people and links pulled out of the conversation as they are said
- Press **Finish** and open the Result tab

## 3. What comes out at the end

- Show notes: summary, chapters with timestamps, title candidates, descriptions, one social post
- The original outline, byte for byte, annotated with what was covered and when
- Transcript as Markdown and as SRT subtitles
- The raw JSON, so the notes can be re-rendered without paying for the model again

**Show:** the sync mark. Press `m` when the real recorder rolls, and every chapter timestamp lines
up with the published audio instead of with the app's clock.

---

## 4. Technology, and why each piece

### Everything that touches audio is local

- **Parakeet TDT 0.6B v3** through MLX on Apple Silicon — 0.25 s per utterance, real-time factor 0.05
- **Silero VAD** to cut utterances, through onnxruntime rather than PyTorch
- Audio never leaves the machine. The only things sent anywhere are outline text and transcript text
- The outline file is opened read-only, always. The app annotates a copy

### Only the reasoning is remote

- **DeepSeek V4 Flash (-fast)** for the tick, every 25 seconds — answers in ~5 s instead of ~38 s
- **Claude Sonnet 5** for the wrap-up, where latency does not matter and the writing does
- Both through **Venice**, so one set of prepaid credits covers both
- A model is written `provider:model`, so moving the wrap-up to Anthropic or OpenAI is one flag

### The part I would defend hardest

- **The LLM proposes; a deterministic reducer disposes.** The model returns candidate changes with
  confidences; ordinary code decides what actually happens to the state
	- a manual mark always wins and locks the model out for ten minutes
	- a heading is only covered when its children are
	- every timestamp the model invents is clamped into the transcript window it actually saw
- Why: a language model that owns your state will eventually rewrite it, live, in front of your guest

### Choices that look boring and are not

- No build step. Vanilla JavaScript, one vendored Markdown renderer, a WebSocket
- The whole state is a JSON file written a second after any change — a crash costs one utterance
- ~13 000 lines of Python, ~400 tests, no PyTorch on macOS

## 5. What it costs

- Ticks: about **$0.0009 each** — most of every prompt comes back from the cache
- Wrap-up: **$0.14–0.20**, driven by the length of the notes it writes, not the transcript it reads
- Measured on a real six-minute session: **$0.17**, 11 ticks, no failures
- A two-hour episode lands around **$0.50**. That was the design target: well under a dollar

## 6. What is honestly not solved

- Speaker labels with one shared microphone: there are none, and the app does not pretend
- Slovak transcription is only as good as the microphone
	- through a Bluetooth headset the model drifted into Polish and Russian
	- forcing the language fixes the drift, not the words
- Parakeet cannot be told what language it is hearing; Whisper can, at ten times the latency
- Coverage thresholds are still a guess that needs a few real episodes

**Closing question:** what would I want it to do that it does not do yet?

---

## Notes to self while recording

- Do not narrate the code. Narrate the map.
- If a topic goes hot that I did not plan, say so out loud — that is the feature working.
- Leave a long silence somewhere so the tick can be seen firing on its own.
