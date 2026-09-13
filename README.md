# In-place Translation

Live English ↔ Chinese sermon translation over church Wi-Fi. One Apple Silicon Mac
listens to the sound desk, translates locally, and sends synchronized voice and
subtitles to visitors' phones. Listeners scan a QR code—there is no app, account,
cloud service, or audio leaving the building.

## What you need

- An Apple Silicon Mac with at least 16 GB of unified memory
- macOS, Homebrew, and about 6 GB of free disk space
- A USB audio interface connected to the sound desk
- Python 3.12 (Python 3.13 or newer is not supported by a required dependency)
- Church Wi-Fi that the Mac and listeners' phones can both join

Plug the Mac into power. A line feed from the sound desk will work much better than
the built-in microphone.

## Install

Keep the project outside Documents, Desktop, and Downloads to avoid extra macOS
permission prompts.

```bash
brew install ffmpeg python@3.12
cd ~
git clone https://github.com/nullbotnone/inplace-translation
cd inplace-translation
python3.12 -m venv .venv
source .venv/bin/activate
pip install speech-to-speech 'misaki[zh]' segno piper-tts
```

The first start downloads roughly 4 GB of local models. macOS will also ask Terminal
for microphone access; allow it.

## Use it on Sunday

1. Connect the USB audio interface and join the church Wi-Fi.
2. Double-click `Start Translation.command` in Finder.
3. Wait for the operator console to open at <http://localhost:8000/admin>.
4. Choose the microphone, spoken language, listener language, model, and voice.
5. Speak into the input and confirm that the level meter and live transcript move.
6. Let listeners scan the QR code and press **Listen live** on their phones.

Leave the Terminal window open. Closing it stops translation and releases the models.

## What listeners receive

The listener page provides translated MP3 audio and live subtitles. The bridge finds
the audible portion of every generated sentence and gives each phone its exact place
in the continuous audio stream. This keeps each translation with its voice even when
the phone buffers, catches up, or receives audio and subtitle packets at different
times.

Sentence timing is synchronized; word timing is approximate because the voice engines
do not provide phoneme timestamps. Chinese reveals by character and English by
approximate syllable timing. With audio paused, translations appear immediately.

## Settings that matter most

- **Microphone:** select the sound-desk input and confirm activity on the meter.
- **Languages:** name the spoken language when possible; automatic detection is useful
  only for genuinely bilingual speech.
- **Glossary:** add ministry names, people's names, and preferred Bible wording. Changes
  apply to the next sentence without restarting.
- **Model:** the default 4B model fits a 16 GB Mac. Larger models improve difficult names
  and Bible references but require more memory.
- **Voice engine:** Kokoro is the default. Piper uses the CPU and is useful when GPU
  contention causes gaps. Qwen3-TTS offers higher quality at lower speed.
- **Voice buffer:** start with 400 ms. The bridge automatically raises it after a voice
  underrun and gradually lowers the extra buffer after stable turns.

Most model or voice changes require a restart; the console tells you when. Microphone,
glossary, target language, and voice-buffer changes apply live where possible.

## Important limits

- Translation begins after the preacher pauses, so it is unsuitable for rapid dialogue.
- Small local models can mistranslate theological language. This is an accessibility aid,
  not the sermon of record.
- Very long speech without pauses increases delay. A backlog over 20 seconds is dropped
  so listeners can return to live audio.
- The experimental audio-in engine requires a 64 GB Mac and an additional large model.

## More documentation

- [Technical and operations guide](TECHNICAL_GUIDE.md)—hardware choices, advanced setup,
  unattended startup, every console setting, tuning, troubleshooting, model cleanup, and
  implementation details
- [Audio-in engine experiments](experiments/README.md)—measurements and setup notes for
  Qwen3-Omni
- [Public project page](https://slashai.app/inplace-translation/)—a friendly overview in
  Simplified Chinese, Traditional Chinese, and English

## Check the installation

```bash
python3 tests/test_bridge.py
python3 tests/check_pages.py
node tests/check_listener.js
node tests/check_console.js
```

For ordinary use, the operator console is the manual. If something unusual happens,
open the [technical guide](TECHNICAL_GUIDE.md) and check `sermon.log`.

## Licence

[Apache License 2.0](LICENSE), copyright 2026 Jie Li. You may use, modify, and
redistribute this project, including in a commercial setting, provided you keep the
notice and state your changes. It comes with no warranty. The models,
`speech-to-speech`, and the voice engines carry their own licences; check those before
redistributing anything built on them.
