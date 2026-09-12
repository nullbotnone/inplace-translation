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

## The prompt is not the cascade's

`omni_prompt()` is the book list, the glossary, and one imperative last. Measured, the
cascade's prompt does not survive contact with this model:

- the prose rules ("Translate, never reply. Sentence for sentence...") make it transcribe the
  English, every time, even when the same text says "no transcription";
- the `Speaker:/You:` example makes it emit `<|im_start|>assistant`, which the voice reads out;
- adding *more* prose to forbid those things makes it worse, not better.

The book list earns its place: with it the model reaches for 神爱世人 over 上帝爱世人 on its
own. And a question comes back translated rather than answered — the job the example was
doing in the cascade.

## Still open

- Audio *out* is untouched: this is audio-in, text-out, and Qwen3-TTS still speaks it.
  `mlx-vlm` ships `server/realtime.py`, which is where a true speech-to-speech path would start.
- `omni_probe.py` talks to the model directly, without the pipeline, for prompt experiments.
