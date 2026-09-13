# Translation over wifi

One laptop listens to the preacher, translates EN↔ZH locally, and broadcasts the
translated voice as an MP3 stream with live subtitles. Phones join by scanning a QR code —
no app, just a browser tab. Nothing leaves the building.

```
                   ┌─▶ ffmpeg ─▶ /stream.mp3 ─┐
mic ─▶ bridge.py ──┼─▶ /subs (subtitles) ─────┴─▶ phones on the church wifi
       │           └─▶ /admin ──────────────────▶ you, on this Mac only
       │
       └─ spawns: run_pipeline.py (speech-to-speech serve)
                    cascade:  VAD → Whisper → translator → Kokoro
                    omni:     VAD → Qwen3-Omni ─────────→ Kokoro
```

`bridge.py` starts the pipeline, so there is one thing to run and one page to drive it.

Both streams come off the same pipeline: the audio deltas become the MP3, the transcripts
become the subtitles. Listeners can use either — audio on headphones, or text only, which
also covers deaf members and anyone the TTS voice doesn't work for.

`speech-to-speech` is a *conversational* agent: VAD cuts the speech into turns, the LLM
"replies", TTS speaks the reply. We keep all of that and only change the system prompt so
the "reply" is a translation. `bridge.py` adds the part it has no concept of: one speaker,
many listeners.

The **omni** engine is the same pipeline with the recogniser removed: the turn's audio goes
straight into a model that hears, and the translation comes back as text for the same voice to
speak. It is opt-in and experimental — `experiments/README.md` has the measurements and the
three things that had to be worked around, including the small proxy `bridge.py` serves at
`/omni/v1` to make the pipeline and the audio model agree on where instructions go.

## Hardware

Apple Silicon only. Everything runs through MLX; there is no CUDA path here.

| | |
|---|---|
| Minimum | M1/M2 with **16 GB** unified memory — Whisper + Qwen3-4B + Kokoro is the default stack; its models download at ~4.3 GB, plus runtime caches |
| Comfortable | M2 Pro / M4 with **24–32 GB**, which buys you the 8 B translator |
| Book names right | **64 GB+**, which buys the 35 B translator — the only one that gets 约翰二书 right — and the optional omni engine |
| Disk | ~6 GB: 4.3 GB of models plus a 1.8 GB virtualenv. The 35 B translator adds 37.7 GB, and the omni engine another 38.8 GB |

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
pip install speech-to-speech 'misaki[zh]' segno   # misaki[zh] is Kokoro's Chinese
                                                  # voices; segno draws the QR code
```

The omni engine is optional and needs a second environment, because `mlx-vlm` pulls a newer
mlx than the pipeline is pinned to. Skip this unless you want it:

```bash
python3 -m venv .venv-omni
.venv-omni/bin/pip install mlx-vlm
.venv-omni/bin/hf download mlx-community/Qwen3-Omni-30B-A3B-Instruct-8bit   # 38.8 GB
```

**Prefer somewhere outside Documents, Desktop and Downloads.** macOS protects those three
folders, so the first run there triggers a permission prompt for Terminal that somebody has to
approve. Anywhere else in your home folder needs no permissions at all.

To move an existing checkout, move the folder and rebuild the environment, since a virtualenv
hard-codes its own path:

```bash
mv ~/Documents/inplace-translation ~/inplace-translation && cd ~/inplace-translation
rm -rf .venv && python3.12 -m venv .venv && source .venv/bin/activate
pip install speech-to-speech 'misaki[zh]' segno   # quick: the wheels are still cached
```

Approving the Terminal prompt works just as well; moving the folder simply means there is no
prompt to explain to whoever runs it next.

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

**Double-click `Start Translation.command` in Finder.** A Terminal window opens, everything
starts, and the console appears in your browser on its own. Leave the window open; closing it
stops the translation and frees the memory the models were holding. Nobody needs to type
anything.

macOS will ask for the microphone the first time, and for folder access if the project sits
somewhere protected. Both prompts come from Terminal, which can actually display them — an
app bundle cannot, which is why this is a `.command` and not a `.app`.

If the project arrived as a downloaded zip rather than a `git clone`, macOS may refuse it with
*"cannot be opened because it is from an unidentified developer"*. Right-click the file once
and choose **Open**, and it will not ask again.

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
  outright gets better recognition on names and short phrases. Detection is limited to these
  two languages, so Mandarin that the recogniser would otherwise have called Japanese still
  comes through as Chinese. Room noise between sentences is thrown away rather than
  translated: handed a cough or the PA's hum, the recogniser returns one word looped to fill
  the clip ("wires, wires, wires, ..."), and the bridge drops it with `!! dropped a looping
  transcription` rather than speaking it over the sermon.
  There is no 简体/繁體 choice, because listeners are hearing audio and the distinction only
  exists in writing. It shows up in the subtitles, so a congregation that reads Traditional
  asks for it in the glossary: *Write all Chinese in Traditional characters (繁體).*
- **Microphone** — pick the input from a list, and watch the level meter while someone talks
  into it. This is the failure everyone hits, and the meter turns it into a five-second check
  instead of a mystery. Switching device takes effect immediately. The list is read when the
  page loads, so **Rescan devices** is there for a headset paired after that. Mics are
  remembered by name, and one that is absent falls back to the system default rather than
  failing — and if a mic disappears mid-sermon the bridge notices within a few seconds and
  reopens it, rather than sitting there translating silence.
- **Listeners** — the QR code to hold up or print, and a count of how many phones are actually
  connected right now.
- **Translation quality** — the engine, the language model, the voice, how much context to
  keep, how long a pause ends a sentence, and how much voice to buffer before playing. Most
  need a restart and the console says so when it matters; the voice buffer applies at once.
  Choosing the omni engine greys out the three settings it does not use — the spoken language,
  the language model and the context — rather than leaving them there to be set pointlessly.
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
- **It stays loaded between services.** `KeepAlive` keeps the models in memory all week so
  Sunday needs no warm-up. If that Mac has other jobs, drop `KeepAlive` and `RunAtLoad` and start it
  with `launchctl kickstart` instead.

## Tuning that actually matters

All of this lives in the console; the notes below are why each one is there.

- **Engine** — *Recognise → translate → speak* is the one to run on a Sunday. *Audio straight
  into the model* is experimental: one model hears the sermon and writes the Chinese, with no
  transcription step in between, which is worth about a quarter of a second a sentence and one
  less model in memory. It needs a 64 GB+ Mac and the second environment from **Setup**;
  `experiments/README.md` has the measurements and what had to be worked around to get a
  conversational model to translate instead of answering.
- **`glossary.txt`** — your ministry names and your elders' names. The single biggest quality
  win available to you. On the cascade the 66 Chinese book names (和合本) are built into the
  prompt, so only name a translation here if your church quotes a different one. The omni
  engine has no such list — it does not need one, and including it made the model read the
  list out loud instead of translating.
  `cp glossary.example.txt glossary.txt`, or just paste into the console. It is gitignored,
  since it ends up full of real people's names. Keep it short — it is re-read on every
  utterance, so a long one costs latency on every sentence of the sermon.
- **One direction at a time.** The console translates the sermon into one language. If you
  need English→Chinese and Chinese→English simultaneously, run a second copy of the repo on
  another port with the directions reversed, and hand out two QR codes.
- **Language model** — cascade only; the omni engine brings its own. 4B is the floor for
  sermon register, 8B is visibly better on a 24 GB+ Mac, and on 64 GB+ the Qwen3.6 35B-A3B is
  the one to pick. It is a mixture of experts with ~3B active, so it costs 0.5 s a sentence
  against the 8B's 0.3 s rather than anything like its size. Watch for backlog warnings after
  a change either way.

  Bible book names are what separates them. The small models calque the English ordinal —
  "Second John" becomes 第二封约翰书 instead of 约翰二书, and 4B renders a bare "John" as
  约翰书 rather than 约翰福音. Scored on 72 spoken book references, greedy: 8B 58/72, 35B
  68/72, and all four of the 35B's misses are it correctly naming the prophet rather than the
  book. No prompt wording fixed the 8B — the variants that helped the numbered books broke
  plain "John". The omni engine gets them right on its own, 6 of 7 including the numbered
  ones, which is why it carries no book list.
- **Context** — cascade only. 2 sentences keeps pronouns and topic consistent without letting
  an hour of sermon fill the context window. Drop to 0 if the model starts chatting back. The
  omni engine always runs at 0: given previous turns it starts answering the conversation
  rather than translating it.
- **Pause before translating** — if the preacher pauses mid-sentence and gets chopped, raise it
  to around 300 ms so clauses stay together.
- **Voice engine** — Kokoro keeps up with the preacher and is the default. Qwen3-TTS sounds
  better; switch to it if the room can spare the speed, and back if `!! backlog, dropping
  audio` appears.
- **Voice** — Kokoro only. Eight Mandarin voices and twenty American English ones, female and
  male; Qwen3-TTS has one voice of its own and the setting greys out. The list follows what
  listeners hear, because a voice comes with the phonemiser for its own language and an
  American voice handed Chinese text reads it as the words "Chinese letter", once per
  character. Changing what listeners hear therefore changes the voice too, and needs a
  restart — the prompt can change mid-service, a loaded voice cannot.
- **Voice buffer** — the model and the voice take turns on the one GPU, so the voice arrives
  in gusts. The bridge buffers this much of it before playing, which is heard as delay rather
  than as stuttering. Raise it if the audio chops, lower it if the voice lags too far behind
  the preacher. 1.5 s is a starting point, not a right answer; tune it by ear in your room.

## What this is not

- **It lags about a second, plus the phone.** Turn-based: nothing is translated until the
  preacher pauses, then recognition, translation, the voice, and the voice buffer all stack
  up. Measured on a recorded sermon from last speech to the first audio leaving the pipeline:
  about 1 s on the cascade, 0.4–0.7 s on omni. The phone's own MP3 buffer adds more on top and
  is not included in those numbers. Fine for preaching, useless for back-and-forth Q&A. Tell
  listeners to use headphones and not to expect lip-sync.
- **Subtitles arrive before the audio they narrate**, by a second or two — the text exists as
  soon as the LLM finishes, the voice has to be synthesised and buffered. Nothing lines them
  up; reading ahead of the voice is the intended behaviour, not a bug to fix.
- **A preacher who never pauses will drift.** Nothing is translated until a pause, so a run of
  speech with no gaps in it is held whole: measured on 18 s of continuous speech, the first
  translated word reached the listener at 20 s. Normal preaching pauses between sentences and
  costs about a second; this is the tail, not the common case.

  The pipeline's `--max_speech_ms` looks like the fix and is not. It does split a long run, but
  each forced split supersedes the one before it, and the earlier fragments are dropped as
  stale: on that same 18 s sample it produced 1.9 s of audio for 20 s of speech — one sentence
  out of six. Leave it at its default. Past 20 s of backlog `bridge.py` drops audio and resyncs
  to live, so a listener hears a gap rather than an ever-growing delay.
- **It will mistranslate.** Local models get theology wrong in interesting ways, the small
  ones spectacularly so. The 35B is the most reliable of the translators and is still a local
  model. Treat it as a hearing aid for visitors, not as the sermon of record.
- **The omni engine has never run a live service.** Everything claimed for it here was
  measured against recorded audio. Each time it met something new it failed in a way nobody
  would have predicted — holding a conversation, reading the Bible book list out loud, opening
  every sentence with the word "assistant". Keep the cascade a click away.

## Where the models live

The first run downloads about 4.3 GB into your home directory, not into the project folder,
so deleting the repo reclaims none of it. Switching the translator in the console downloads
that model too, the first time you select it: the 8B is 4.6 GB, the 35B is 37.7 GB and the omni
engine's model is 38.8 GB, and none of them replaces what is already there. The omni model lands
in the same cache even though it runs from `.venv-omni`.

| | |
|---|---|
| `~/.cache/huggingface/hub` | recognition, translation and voice models, ~4.3 GB, plus any translator or voice engine you switched to |
| `~/.cache/torch/hub` | Silero voice activity detection, a few MB |

To clear them, activate this project's environment and remove them by name:

```bash
source .venv/bin/activate
hf cache ls                    # what is cached, and how big

hf cache rm model/mlx-community/Qwen3-4B-Instruct-2507-4bit \
            model/mlx-community/Kokoro-82M-bf16 \
            model/mlx-community/whisper-large-v3-turbo

# only if you switched the translator or the voice engine in the console, or ran
# a version that used Smart Turn; hf cache ls above shows which you actually have
hf cache rm model/mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-6bit \
            model/mlx-community/Qwen3-8B-4bit \
            model/mlx-community/Qwen3.6-35B-A3B-8bit \
            model/mlx-community/Qwen3-Omni-30B-A3B-Instruct-8bit \
            model/pipecat-ai/smart-turn-v3

hf cache prune                 # half-finished downloads
```

**Do not just `rm -rf ~/.cache/huggingface`.** That directory is shared by every Hugging Face
tool on the Mac and very likely holds models belonging to your other work. Run `hf cache ls`
first, and add `--dry-run` to preview what a removal would take. Once removed, the next start
downloads them again and the console sits on "starting" until it finishes.

## Self-check

```bash
python3 test_bridge.py   # pacing, backlog drop, listener eviction, subtitle fan-out, config
                         # validation, clean shutdown on TERM/HUP, console refuses the LAN,
                         # the mic watchdog, and the prompt and proxy the omni engine needs
python3 check_pages.py   # every label in 简/繁/EN, both themes complete, no dead ids, the
                         # theme toggle always flips, and the console script runs against both
                         # engines (needs node)
```

The pipeline itself has no self-check here: start it and read `sermon.log`, where every
transcript and its translation is printed as it happens.
