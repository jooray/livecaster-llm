# Livecaster, a live co-pilot for podcast hosts

**What this is:** a screencast about the tool that is transcribing this recording.
**Format:** solo, one microphone, about 15 minutes.
**Rule for myself:** show it running before explaining how it works.

---

## 1. The problem

- I record long interviews and I write an outline for every one of them
- Halfway through a conversation I lose track of what we already covered, and I find out weeks later in the edit
- Two ways it goes wrong: I ask something we already answered, or I forget the one question I actually cared about
- Paper notes do not update themselves. A second screen full of text is worse than nothing

**Question to answer on camera:** why is this not just "run Whisper afterwards"?

## 2. What it does

- Open the map. The outline is the whole interface
- Talk about a topic, wait, watch the line strike through with a timestamp on it
- The three states: ~~covered~~, ◐ touched, ⏭ skipped
	- covered needs a verbatim quote from the transcript before it counts
	- a passing mention only gets "touched"
- Now and Next: one line, then three labels of five words
	- press `d` to see the model's reasoning and the segue it suggests
	- you never read a paragraph while you are talking
- Press `c` on something the model missed. It then stays out of that item for ten minutes
- Mentions: books, people and links pulled out of the conversation as they are said
- Press Finish and open the Result tab

## 3. What comes out at the end

Show notes with a summary, chapters, title candidates, descriptions and one social post. The
original outline, byte for byte, annotated with what was covered and when. The transcript as
Markdown and as SRT. The raw JSON, so the notes can be re-rendered without paying the model again.

Show the sync mark. Press `m` when the real recorder rolls and every chapter timestamp lines up
with the published audio instead of with the app's clock.

---

## 4. Technology, and why each piece

### Everything that touches audio is local

- Parakeet TDT 0.6B v3 through MLX on Apple Silicon: 0.25 s per utterance, real-time factor 0.05
- Silero VAD cuts the utterances, through onnxruntime rather than PyTorch
- Audio never leaves the machine. Only outline text and transcript text go anywhere
- The outline file is opened for reading and never for writing. The app annotates a copy

### Only the reasoning is remote

- DeepSeek V4 Flash (-fast) for the tick, every 25 seconds. It answers in about 5 s; the plain model takes 38
- Claude Sonnet 5 for the wrap-up. Nobody is waiting on it, so it can be slow and write well
- Both through Venice, so one set of prepaid credits covers them
- A model is written `provider:model`, so moving the wrap-up to Anthropic or OpenAI is one flag

### The part I would defend hardest

The model proposes and ordinary code decides. A tick comes back as candidate changes with
confidences attached, and a deterministic reducer decides what actually happens to the state.

- a manual mark always wins, and locks the model out of that item for ten minutes
- a heading is covered only once its own items are
- every timestamp the model invents gets clamped into the transcript window it actually saw

If the model owned the state directly it could rewrite the map mid-interview, and I would have no
way to stop it while talking to someone.

### The unglamorous parts

- No build step: vanilla JavaScript, one vendored Markdown renderer, a WebSocket
- The whole state is a JSON file written a second after any change, so a crash costs one utterance
- About 13 000 lines of Python, about 400 tests, no PyTorch on macOS

## 5. What it costs

- A tick costs about $0.0009, because most of every prompt comes back from the cache
- The wrap-up costs $0.14 to $0.20, set by the length of the notes it writes rather than the transcript it reads
- Measured on a real six-minute session: $0.17, 11 ticks, nothing failed
- A two-hour episode lands around $0.50. The target was under a dollar

## 6. What is not solved

- With one shared microphone there are no speaker labels, and the app does not pretend otherwise
- Slovak transcription is only as good as the microphone
	- through a Bluetooth headset the model drifted into Polish and once into Russian
	- forcing the language fixes the drift, not the words
- Parakeet cannot be told what language it is hearing. Whisper can, at ten times the latency
- The coverage thresholds are still a guess that needs a few real episodes

**Closing question:** what would I want it to do that it does not do yet?

---

## Notes to self while recording

- Do not narrate the code. Narrate the map
- If a topic goes hot that I did not plan, say so out loud. That is the feature working
- Leave a long silence somewhere so the tick fires on camera
