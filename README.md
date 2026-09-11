# Sermon translation over the church wifi

One laptop listens to the preacher, translates EN↔ZH locally, and broadcasts the
translated voice as an MP3 stream with live subtitles. Phones join by scanning a QR code —
no app, just a browser tab. Nothing leaves the building.

```
mic ──▶ speech-to-speech serve ──▶ bridge.py ──┬─▶ ffmpeg ──▶ /stream.mp3 ──┐
        (VAD → STT → LLM → TTS)    (fan-out)    └─▶ SSE ──────▶ /subs ───────┴─▶ phones
```

Both streams come off the same pipeline: the audio deltas become the MP3, the transcripts
become the subtitles. Listeners can use either — audio on headphones, or text only, which
also covers deaf members and anyone the TTS voice doesn't work for.

`speech-to-speech` is a *conversational* agent: VAD cuts the speech into turns, the LLM
"replies", TTS speaks the reply. We keep all of that and only change the system prompt so
the "reply" is a translation. `bridge.py` adds the part it has no concept of: one speaker,
many listeners.

## Hardware

Apple Silicon only. Everything runs through MLX; there is no CUDA path here.

| | |
|---|---|
| Minimum | M1/M2 with **16 GB** unified memory — Whisper + Qwen3-4B + Qwen3-TTS is ~7.5 GB of weights, plus caches |
| Comfortable | M2 Pro / M4 with **24–32 GB**, which buys you the 8 B translator |
| Disk | ~15 GB for weights and dependencies |

Plug the laptop in and run it from the wall. A 40-minute sermon is 40 minutes of sustained
MLX inference; on battery the Mac throttles and the translation falls behind.

**Feed it line audio, not the built-in mic.** A USB interface taken off the sound desk's
aux/monitor out beats every model upgrade on this page. Room mics pick up the congregation,
the HVAC, and the PA's own output, and Whisper transcribes all of it.

## Setup

```bash
brew install ffmpeg
python3 -m venv .venv && source .venv/bin/activate
pip install speech-to-speech segno      # segno is optional, only for the terminal QR
```

macOS will ask Terminal for microphone permission the first time `bridge.py` runs. If the
prompt never appears, grant it by hand in System Settings → Privacy & Security → Microphone.

## Run

Terminal 1 — the pipeline. First run downloads ~8 GB and warms the models up:

```bash
caffeinate -i speech-to-speech serve \
    --mac-optimal-settings \
    --stt mlx-audio-whisper --language auto \
    --model_name mlx-community/Qwen3-4B-Instruct-2507-4bit \
    --chat_size 2 --num_pipelines 1
```

`--mac-optimal-settings` picks the MLX stack, but its default Parakeet STT has no Chinese, so
we override it: `mlx-audio-whisper` is built into the macOS install and multilingual.
`--language auto` detects each utterance's language, which is what lets one pipeline carry
both directions. Check that backend's model flag with
`speech-to-speech serve --stt mlx-audio-whisper -h` and pin a `large-v3` checkpoint if it
defaults to something smaller. (`--stt whisper-mlx` is the documented alternative; it needs
`pip install "speech-to-speech[whisper-mlx]"`.)

`caffeinate -i` stops macOS idle-sleeping mid-sermon and killing both processes.

Terminal 2 — the broadcast:

```bash
python3 -m sounddevice                  # find your USB interface
caffeinate -i python3 bridge.py --device "Scarlett 2i2"
```

It prints the listener URL and a QR code. Print the QR on the bulletin or put it on a slide,
and give the laptop a static DHCP reservation so the URL never changes.

## Running it without you

`./start.sh` does both terminals: it launches the pipeline, waits for :8765 to answer, starts
the bridge, and kills both together on exit. Edit the defaults at the top or override them:

```bash
AUDIO_DEVICE="Scarlett 2i2" ./start.sh
```

To have it come up by itself, install the **LaunchAgent** — an agent, not a daemon, because
microphone access is granted per logged-in user and a root daemon can never get it:

```bash
sed "s|__DIR__|$PWD|g" com.church.sermon-translate.plist \
    > ~/Library/LaunchAgents/com.church.sermon-translate.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.church.sermon-translate.plist
```

Set `AUDIO_DEVICE` and `MODEL` inside that plist — they're the only two knobs in it.

```bash
tail -f sermon.log                                               # what it's doing
launchctl kickstart -k gui/$(id -u)/com.church.sermon-translate  # restart it
launchctl bootout gui/$(id -u)/com.church.sermon-translate       # stop it for good
```

Four things to get right on a Mac that nobody logs into on Sunday morning:

- **Run `./start.sh` by hand once first** and grant the microphone prompt. Under launchd the
  prompt is easy to miss, and a denied mic looks exactly like silence in the sanctuary.
- **Turn on automatic login** (System Settings → Users & Groups). A LaunchAgent starts at
  login, so a Mac sitting at the login screen is running nothing.
- **Stop it sleeping** (System Settings → Lock Screen → never; Displays → prevent sleep when
  the display is off). `caffeinate -i` covers idle sleep, not a scheduled or lid-close sleep.
- **It stays loaded between services.** `KeepAlive` holds ~8 GB resident all week so Sunday
  needs no warm-up. If that Mac has other jobs, drop `KeepAlive` and `RunAtLoad` and start it
  with `launchctl kickstart` instead.

## Tuning that actually matters

- **`glossary.txt`** — if present, its contents are appended to the translator prompt. Put
  your church's names in it, your ministry names, and the Bible translation you quote (和合本
  vs 新译本 wording). This is the single biggest quality win available to you:
  `cp glossary.example.txt glossary.txt` and edit. It is gitignored, since it ends up full of
  real people's names. Keep it short — it is re-read on every utterance, so a long one costs
  latency on every sentence of the sermon.
- **LLM size** — a 4B model is the floor for sermon register. On a 24 GB+ Mac use
  `mlx-community/Qwen3-8B-Instruct-4bit`; translation quality scales visibly. Watch the
  backlog warnings after you switch — a bigger model is also a slower one.
- **`--chat_size`** — 2 keeps a little context for pronoun/topic consistency without letting
  an hour of sermon fill the context window. 0 if you see the model drifting into chat.
- **VAD** — if the preacher pauses mid-sentence and gets chopped, raise
  `--min_silence_ms` (try 300) so clauses stay together.
- **TTS** — Qwen3-TTS sounds best. If `!! backlog, dropping audio` keeps firing and a smaller
  LLM hasn't fixed it, `--tts kokoro` is built into the macOS install and much faster.

## What this is not

- **It lags 3–8 s.** Turn-based: nothing is translated until the preacher pauses, then STT +
  LLM + TTS + the phone's MP3 buffer all stack up. Fine for preaching, useless for
  back-and-forth Q&A. Tell listeners to use headphones and not to expect lip-sync.
- **Subtitles arrive before the audio they narrate**, by a second or two — the text exists as
  soon as the LLM finishes, the voice has to be synthesised and buffered. Nothing lines them
  up; reading ahead of the voice is the intended behaviour, not a bug to fix.
- **A preacher who never pauses will drift.** TTS output is roughly as long as the input, so
  there's no slack to catch up. Past 20 s of backlog `bridge.py` drops audio and resyncs to
  live — a listener hears a gap, not an ever-growing delay.
- **It will mistranslate.** Local 4–8B models get theology wrong in interesting ways. Treat
  it as a hearing aid for visitors, not as the sermon of record.

## Self-check

```bash
python3 test_bridge.py   # pacing, backlog drop, listener eviction, subtitle fan-out
```

The pipeline itself has no self-check here: start it and read `sermon.log`, where every
transcript and its translation is printed as it happens.
