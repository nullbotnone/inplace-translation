#!/usr/bin/env python3
"""Probe an audio-in ("omni") model as a replacement for the Whisper -> LLM stages.

The pipeline can already do this: `--stt none` sends VAD audio straight to an audio-input
LLM, but only through the `chat-completions` backend, i.e. an OpenAI-compatible server. This
script stands one up with mlx-vlm and measures it against the cascade on the same audio.

    python3 experiments/omni_probe.py path/to/speech.wav

Needs a venv that is NOT this project's: mlx-vlm pulls mlx 0.32.2 / mlx-audio 0.5.3, and the
pipeline is pinned to 0.32.0 / 0.4.7 (see speech_to_speech/utils/mlx_lock.py for why that
pin exists). Build it with:

    python3 -m venv /tmp/omni && /tmp/omni/bin/pip install mlx-vlm
    /tmp/omni/bin/hf download mlx-community/Qwen3-Omni-30B-A3B-Instruct-8bit   # 36 GB
    /tmp/omni/bin/python -m mlx_vlm.server --model <that model> --port 8770
"""
import base64, json, sys, time, urllib.error, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import bridge

MODEL = "mlx-community/Qwen3-Omni-30B-A3B-Instruct-8bit"
URL = "http://127.0.0.1:8770/v1/chat/completions"


def ask(wav: bytes, text: str, *, system: str | None = None, max_tokens: int = 200):
    """One request. `text` rides in the user turn, after the audio -- order matters."""
    content = [{"type": "input_audio",
                "input_audio": {"data": base64.b64encode(wav).decode(), "format": "wav"}}]
    if text:
        content.append({"type": "text", "text": text})
    messages = ([{"role": "system", "content": system}] if system else []) + \
               [{"role": "user", "content": content}]
    body = {"model": MODEL, "messages": messages, "max_tokens": max_tokens, "temperature": 0}
    req = urllib.request.Request(URL, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    r = json.load(urllib.request.urlopen(req, timeout=600))
    return time.perf_counter() - t0, r["choices"][0]["message"]["content"].strip()


def main():
    if len(sys.argv) < 2:
        return __doc__.strip().splitlines()[0] + "\n\n    omni_probe.py path/to/speech.wav"
    wav = Path(sys.argv[1]).read_bytes()
    prompt = bridge.instructions({**bridge.DEFAULTS, "source": "en", "target": "zh"})
    short = "Translate into Chinese. Output only the Chinese translation."

    for label, kwargs in (
        # How the pipeline would send it, and the reason this is not wired up yet.
        ("as the pipeline sends it (system prompt)", dict(text="", system=prompt)),
        ("prompt in the user turn, after the audio", dict(text=prompt)),
        ("a short instruction instead", dict(text=short)),
        # Instruction before the audio: it transcribes rather than translates.
        ("instruction before the audio", dict(text="", system=None)),
    ):
        try:
            t, out = ask(wav, **kwargs)
            print(f"{t:5.2f}s  {label}\n        {out[:160]!r}")
        except urllib.error.HTTPError as exc:
            print(f"   --   {label}: HTTP {exc.code} {exc.read().decode()[:120]}")


if __name__ == "__main__":
    sys.exit(main())
