# Audio-in ("omni") instead of Whisper → LLM

Measured on an M5 Max, 2026-09-11, against the same 15.1 s recorded sermon used for the
latency work on `main`. Model: `mlx-community/Qwen3-Omni-30B-A3B-Instruct-8bit` (36 GB),
served by `mlx-vlm` 0.7.0, which implements `qwen3_omni_moe` and an OpenAI server that
accepts `input_audio`. Reproduce with `omni_probe.py`.

## It works, and it is quick

Audio straight in, Chinese straight out, no transcription step:

| | one sentence (3.3 s) | the whole 15.1 s clip |
|---|---|---|
| full sermon prompt, in the user turn | 0.57 s | 1.5 s |
| a one-line instruction instead | 0.35 s | 0.8 s |

For comparison, the cascade on `main` spends ~0.2–0.3 s in Whisper plus 0.52 s in the 35B
for one sentence. So dropping the STT stage is worth roughly 0.2–0.4 s a sentence. The TTS
stage stays either way; this is audio-in, text-out, not audio-out.

Translation quality held on the sample, book name included:

> 因为神爱世人，甚至将他的独生子赐给他们。今天早上我想跟你们分享三件事。第一件是关于祷告，
> 以及它如何改变我们。请跟我翻到约翰福音第三章。

## Two things block wiring it up

**1. The server mishandles `system` messages for this model**, and a system message is
exactly how the pipeline sends our instructions. Any request carrying one comes back with
chat-template tokens and a truncated answer:

    system="Translate the audio into Chinese"  →  '<|im_start|>user\nFor God so loved the world…'
    our full prompt as system                  →  '<|im_start|>user\nJohn three'

The same prompt sent as a text part *in the user turn, after the audio* translates correctly.
Order matters too: put the instruction before the audio and it transcribes to English instead
of translating. Wiring this up therefore needs a small proxy between `speech-to-speech` and
`mlx-vlm` that moves the system message into the user turn — patching the dependency is not
an option, and the pipeline has no flag for it.

**2. Template tokens leak into the output.** With the full prompt the reply is prefixed
`<|im_start|>assistant`, which would land in the subtitles and be read aloud by the TTS. The
short instruction does not trigger it, so it is probably our prompt's `Speaker:/You:` example
being continued as dialogue. Needs a prompt shaped for this model, and output sanitising.

## Notes

- Install into a separate venv. `mlx-vlm` pulls mlx 0.32.2 / mlx-audio 0.5.3; the pipeline is
  pinned to 0.32.0 / 0.4.7, and that pin is deliberate (`utils/mlx_lock.py`).
- Memory is roughly a wash: 36 GB of omni replaces 35 GB of translator plus Whisper.
- `mlx-vlm` also ships `server/realtime.py`. Not explored; that is the road to audio-out.
