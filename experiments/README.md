# Audio-in ("omni") instead of Whisper → LLM

Wired up and working. Pick **Engine → Audio straight into the model** in the console, or set
`"engine": "omni"` in `config.json`. Measured on an M5 Max, 2026-09-12.

## One-time setup

`mlx-vlm` pulls a newer mlx than the pipeline is pinned to (0.32.2 against 0.32.0, and
mlx-audio 0.5.3 against 0.4.7), and that pin is deliberate — see `utils/mlx_lock.py`. So the
audio model gets its own venv, which `bridge.py` launches on demand:

```bash
python3 -m venv .venv-omni
.venv-omni/bin/pip install mlx-vlm
.venv-omni/bin/hf download mlx-community/Qwen3-Omni-30B-A3B-Instruct-8bit   # 36 GB
```

Needs a 64 GB+ Mac. Nothing else changes: the same VAD, the same Qwen3-TTS voice, the same
console. Only the recognise-then-translate pair is replaced by one model that hears.

## What it buys

Same 15.1 s recorded sermon, each engine measured with only its own models resident. Last
speech detected to first speech out, per sentence:

| | 1 | 2 | 3 | 4 | audio for 15.1 s of speech |
|---|---|---|---|---|---|
| cascade (Whisper + 35B) | 1.00 s | 0.93 s | 0.98 s | 0.96 s | 14.3 s |
| omni (Qwen3-Omni 30B-A3B) | 0.79 s | 0.92 s | 0.65 s | 0.59 s | 12.9 s |

About a quarter of a second a sentence, and one model in memory instead of two. Both
translate correctly, book name included: `请跟我一起翻到约翰福音第三章`.

## Three things had to be worked around

**The pipeline sends instructions as a system message, and mlx-vlm's server mangles those
for this model** — the answer comes back as chat-template tokens and a fragment
(`'<|im_start|>user\nJohn three'`). The same text works when it rides in the user turn,
*after* the audio; before the audio it transcribes instead of translating. The proxy in
`bridge.py` (`Handler.omni_proxy`) moves it.

**The instructions arrive wrapped in a voice-assistant envelope** — a lead about being in a
spoken conversation and a `## Voice Rules` tail about keeping replies brief and treating
transcripts as noisy. Prose about being an assistant is exactly what makes this model
transcribe rather than translate, so the proxy keeps the `Session Prompt:` section and drops
the envelope.

**Conversation history makes it answer the chat instead of translating it.** With previous
turns in the request it began prefixing replies with `Assistant:`, and by the fourth turn it
was reading the Bible book list out loud instead of the sermon. Omni runs with `chat_size 0`;
`chat_size` stays a cascade-only setting.

## The prompt is not the cascade's, and it is shorter than you would think

`omni_prompt()` is one imperative, repeated either side of the glossary. Nothing else.
Scored over eight sermon utterances that invite a reply — "Can I get an amen?", "Good
morning, how are you all doing?", "What is grace?":

| prompt | translated |
|---|---|
| the cascade's prompt | transcribes; the Speaker:/You: example is read out as `<\|im_start\|>` |
| any "never answer the speaker" rule | 4–5 of 8; the rest come back as a bare `<\|im_start\|>` |
| book list + one imperative | **4 of 8** — for short utterances it reads the list itself aloud |
| one imperative, repeated around the glossary | **8 of 8** |

The book list had to go. It is what made the model read 66 book names out loud instead of
translating "Can I get an amen?", and it buys nothing: without it the model still gets 6 of 7
book names right, 提摩太后书, 哥林多前书 and 约翰三书 among them — the numbered books the
cascade's 8B got wrong. The seventh is 哈巴谷 for "Habakkuk tells us", which is the prophet
speaking and correct.

Rules do not survive either, however they are phrased. Prose about being an assistant is what
tips this model from translating into transcribing, so the safeguard against it answering the
preacher is not a rule but the absence of everything else. Verified end to end: "What is
grace?" comes back 什么是恩典？ rather than a definition.

## Still open

- Audio *out* is untouched: this is audio-in, text-out, and Qwen3-TTS still speaks it.
  `mlx-vlm` ships `server/realtime.py`, which is where a true speech-to-speech path would start.
- `omni_probe.py` talks to the model directly, without the pipeline, for prompt experiments.
