# Translation over wifi

One laptop listens to the preacher, translates EN↔ZH locally, and broadcasts the
translated voice as an MP3 stream with live subtitles. Phones join by scanning a QR code —
no app, just a browser tab. Nothing leaves the building.

```
                   ┌─▶ ffmpeg ─▶ /stream.mp3 ─┐
mic ─▶ bridge.py ──┼─▶ /subs (subtitles) ─────┴─▶ phones on the church wifi
       │           └─▶ /admin ──────────────────▶ you, on this Mac only
       │
       └─ spawns: speech-to-speech serve  (VAD → STT → LLM → TTS)
```

`bridge.py` starts the pipeline, so there is one thing to run and one page to drive it.

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
brew install ffmpeg python@3.12
cd ~                                    # not Documents, Desktop or Downloads — see below
git clone https://github.com/nullbotnone/inplace-translation
cd inplace-translation
python3.12 -m venv .venv && source .venv/bin/activate
pip install speech-to-speech segno      # segno is optional, only for the QR code
```

**Keep the project out of Documents, Desktop and Downloads.** macOS protects those three
folders, and an app made of a shell script has no run loop, so it cannot even show the
permission prompt — the read simply fails with *"Operation not permitted"*. Your home folder,
or anywhere outside those three, works with no permissions at all. The app checks this on
launch and explains it rather than showing the raw error.

If it is already in the wrong place, move the folder and rebuild the environment, since a
virtualenv hard-codes its own path:

```bash
mv ~/Documents/inplace-translation ~/inplace-translation && cd ~/inplace-translation
rm -rf .venv && python3.12 -m venv .venv && source .venv/bin/activate
pip install speech-to-speech segno      # quick: pip still has the wheels cached
```

Granting the app Full Disk Access in System Settings → Privacy & Security works too, but
moving the folder is less to explain to whoever runs it next.

**Use Python 3.12, not whatever `python3` points at.** `misaki`, which Kokoro's text
processing pulls in, publishes nothing for 3.13 or newer. On a 3.13+ venv pip cannot resolve
it, backtracks through every `speech-to-speech` release looking for one that does not need it,
and finally prints a `ResolutionImpossible` wall of text that names fourteen versions and
never says the word "Python". The one line that matters in it is:

```
Additionally, some packages in these conflicts have no matching distributions
available for your environment:
    misaki
```

If you hit that, `rm -rf .venv` and rebuild it with `python3.12`. `start.sh` checks the
version at startup so it cannot bite you twice.

macOS will ask Terminal for microphone permission the first time `bridge.py` runs. If the
prompt never appears, grant it by hand in System Settings → Privacy & Security → Microphone.

## Run

**Double-click `Sermon Translation.app`.** It starts everything and opens the console in your
browser; there is no Terminal window and nothing to type. Quitting it from the Dock stops the
translation and frees the memory the models were holding. Double-clicking it again while it is
already running just brings the console back up.

Keep the app inside the project folder — it finds `start.sh` next to itself, and says so if
you move it. Drag it to the Dock for a shortcut rather than to Applications.

The first time, macOS may refuse it with *"cannot be opened because it is from an
unidentified developer"* — that happens when the project arrived as a downloaded zip rather
than a `git clone`. Right-click it once and choose **Open**, and it will not ask again. It
will also ask for the microphone the first time; that prompt comes from the app, and it has
to be allowed.

Anything it prints goes to `sermon.log`, and a failed start shows a dialog with the last few
lines.

From a terminal, the same thing:

```bash
./start.sh
```

That is the whole thing. It launches the pipeline, waits for the models to load, starts the
broadcast, and prints the listener URL and a QR code. Then open the console:

**http://localhost:8000/admin**

The console is where everything gets configured, so you should not need this README again.
It reads 简 / 繁 / EN and has a day/night toggle in the corner, both remembered between
sessions:

- **Sermon languages** — what the preacher speaks, and what listeners hear. These are not the
  简/繁/EN switcher in the corner, which only changes the console's own wording. The spoken
  language sets what the recogniser listens for and needs a restart; the listeners' language
  only rewrites the prompt, so it takes effect on the next sentence. Pick **Detect
  automatically** only if the preacher genuinely switches mid-sermon — naming the language
  outright gets better recognition on names and short phrases.
  There is no 简体/繁體 choice, because listeners are hearing audio and the distinction only
  exists in writing. It shows up in the subtitles, so a congregation that reads Traditional
  asks for it in the glossary: *Write all Chinese in Traditional characters (繁體).*
- **Microphone** — pick the input from a list, and watch the level meter while someone talks
  into it. This is the failure everyone hits, and the meter turns it into a five-second check
  instead of a mystery. Switching device takes effect immediately.
- **Listeners** — the QR code to hold up or print, and a count of how many phones are actually
  connected right now.
- **Translation quality** — the language model, the voice, how much context to keep, and how
  long a pause ends a sentence. These need a restart, and the console says so when it matters.
- **Glossary** — edit it in the browser. Saved changes apply to the very next sentence, no
  restart, because the prompt is rebuilt per session update.
- **Live transcript** — what was heard and what was said, as it happens.

The console binds to localhost only, so nobody on the church wifi can stop your broadcast from
their phone. Use the Mac itself.

Settings are stored in `config.json` next to the script. You can edit that file instead if you
prefer; the console just writes the same file.

## Running it without you

To have it come up by itself, install the **LaunchAgent** — an agent, not a daemon, because
microphone access is granted per logged-in user and a root daemon can never get it:

```bash
sed "s|__DIR__|$PWD|g" com.church.sermon-translate.plist \
    > ~/Library/LaunchAgents/com.church.sermon-translate.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.church.sermon-translate.plist
```

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

All of this lives in the console; the notes below are why each one is there.

- **`glossary.txt`** — your ministry names, your elders' names, and the Bible translation you
  quote (和合本 vs 新译本 wording). The single biggest quality win available to you.
  `cp glossary.example.txt glossary.txt`, or just paste into the console. It is gitignored,
  since it ends up full of real people's names. Keep it short — it is re-read on every
  utterance, so a long one costs latency on every sentence of the sermon.
- **One direction at a time.** The console translates the sermon into one language. If you
  need English→Chinese and Chinese→English simultaneously, run a second copy of the repo on
  another port with the directions reversed, and hand out two QR codes.
- **Language model** — 4B is the floor for sermon register. On a 24 GB+ Mac pick the 8B; the
  difference is visible. Watch for backlog warnings afterwards — a bigger model is a slower one.
- **Context** — 2 sentences keeps pronouns and topic consistent without letting an hour of
  sermon fill the context window. Drop to 0 if the model starts chatting back.
- **Pause before translating** — if the preacher pauses mid-sentence and gets chopped, raise it
  to around 300 ms so clauses stay together.
- **Voice** — Qwen3-TTS sounds best. If `!! backlog, dropping audio` keeps appearing and a
  smaller model has not fixed it, switch to Kokoro.

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

## Where the models live

The first run downloads about 6.6 GB into your home directory, not into the project folder,
so deleting the repo reclaims none of it.

| | |
|---|---|
| `~/.cache/huggingface/hub` | recognition, translation and voice models, ~6.6 GB |
| `~/.cache/torch/hub` | Silero voice activity detection, a few MB |

To clear them, activate this project's environment and remove them by name:

```bash
source .venv/bin/activate
hf cache ls                    # what is cached, and how big

hf cache rm model/mlx-community/Qwen3-4B-Instruct-2507-4bit \
            model/mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-6bit \
            model/mlx-community/whisper-large-v3-turbo \
            model/pipecat-ai/smart-turn-v3

hf cache prune                 # half-finished downloads
```

**Do not just `rm -rf ~/.cache/huggingface`.** That directory is shared by every Hugging Face
tool on the Mac and very likely holds models belonging to your other work. Run `hf cache ls`
first, and add `--dry-run` to preview what a removal would take. Once removed, the next start
downloads them again and the console sits on "starting" until it finishes.

## Self-check

```bash
python3 test_bridge.py   # pacing, backlog drop, listener eviction, subtitle fan-out,
                         # config persistence, and that the console refuses the LAN
python3 check_pages.py   # every label in 简/繁/EN, both themes complete, no dead ids,
                         # and the console script actually runs (needs node)
```

The pipeline itself has no self-check here: start it and read `sermon.log`, where every
transcript and its translation is printed as it happens.
