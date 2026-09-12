#!/usr/bin/env python3
"""Sermon translator bridge.

Owns the whole thing: starts the speech-to-speech pipeline, feeds it the mic,
broadcasts the translated audio and subtitles to phones on the LAN, and serves
an operator console at http://localhost:8000/admin

    python3 bridge.py
"""
import argparse, array, base64, contextlib, json, queue, re, signal, socket, subprocess, sys, threading, time
import urllib.error, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
S2S_PORT, HTTP_PORT = 8765, 8000
RATE, BLOCK = 16000, 320          # 20 ms of s16le mono
MAX_LAG_S = 20                    # translation backlog before we start dropping
BITRATE = 48000                   # mp3 bits per second
MP3_CHUNK = 256                   # one mp3 frame at this bitrate and rate, in bytes. Reading
                                  # 1024 held four frames back: 303 ms before the first byte
                                  # left the encoder, against 124 ms a frame at a time.
MAX_QUEUED = 10 * BITRATE // 8 // MP3_CHUNK   # ~10 s buffered per listener before eviction
OMNI_PORT = 8770                  # the audio-in model's own OpenAI server
OMNI_MODEL = "mlx-community/Qwen3-Omni-30B-A3B-Instruct-8bit"
OMNI_PYTHON = HERE / ".venv-omni/bin/python"   # mlx-vlm needs its own venv; see README
MIC_DEAD_S = 3                    # no callback for this long means the input is gone
LOAD_TIMEOUT_S = 3600             # the port stays shut until the models are downloaded, and
                                  # the 35B option is a 35 GB first run. A pipeline that dies
                                  # is caught by poll(), so this only backstops a live hang.

CONFIG_PATH = HERE / "config.json"
GLOSSARY_PATH = HERE / "glossary.txt"
DEFAULTS = {
    "device": None,                                              # mic, by name; None = system default
    "source": "en",                                              # what the preacher speaks
    "target": "zh",                                              # what listeners hear
    "model": "mlx-community/Qwen3-4B-Instruct-2507-4bit",
    "stt": "mlx-audio-whisper",
    "tts": "qwen3",
    "chat_size": 2,
    "min_silence_ms": 64,
    "lead_ms": 1500,                                             # voice buffered before it plays
    "engine": "cascade",                                         # cascade | omni
}
NEEDS_RESTART = {"model", "stt", "tts", "chat_size", "min_silence_ms", "source", "engine"}
# device, target, lead_ms and the glossary apply live: the first reopens the mic, the next two
# only change the prompt and a buffer. "source" sets the recognition language, a CLI flag.

SPOKEN = {"en": "English", "zh": "Chinese", "auto": "whatever language the speaker uses"}
TARGETS = {"en": "English", "zh": "Chinese"}
# Listeners hear audio, where 简体 vs 繁體 does not exist. It only shows up in the
# subtitles, so a church that wants Traditional asks for it in the glossary instead.
LEGACY_TARGET = {"zh-Hans": "zh", "zh-Hant": "zh"}

# The console posts these, and a hand-edited config.json can hold anything. An unknown
# value here would reach a CLI flag or a dict lookup, so reject it at the door.
CHOICES = {"source": set(SPOKEN), "target": set(TARGETS), "tts": {"qwen3", "kokoro"},
           "engine": {"cascade", "omni"}}
BOUNDS = {"chat_size": (0, 8), "min_silence_ms": (32, 2000), "lead_ms": (200, 8000)}


def valid(key, value):
    if key in CHOICES:
        return value in CHOICES[key]
    if key in BOUNDS:
        lo, hi = BOUNDS[key]
        return isinstance(value, int) and not isinstance(value, bool) and lo <= value <= hi
    if key == "device":
        # Names, not indices: an index shuffles every time a Bluetooth mic comes or goes, so
        # a saved one silently opens some other microphone. An index left in an old config is
        # rejected on load and the console falls back to the system default.
        return value is None or (isinstance(value, str) and value.strip() != "")
    return isinstance(value, str) and value.strip() != ""

# One worked pair per direction. A small model that is *told* not to answer still answers;
# shown one utterance it would rather reply to, it stops. The example is also the one piece
# of text it will happily borrow from, so it carries no names, no numbers and no scripture
# reference: an example holding "John 3" is how a bare 约翰福音 came back out as "John 3".
EXAMPLE = {
    "zh": ("Do you know what that means?", "你知道那是什么意思吗？"),
    "en": ("你知道那是什么意思吗？", "Do you know what that means?"),
}

# The 66 book names, English = 和合本. A 4B model guesses at the rarer ones and invents
# chapter numbers to go with them; sermons are mostly book names, so pin them here rather
# than leaving each church to type them into the glossary.
BIBLE_BOOKS = """Genesis = 创世记 | Exodus = 出埃及记 | Leviticus = 利未记 | Numbers = 民数记
Deuteronomy = 申命记 | Joshua = 约书亚记 | Judges = 士师记 | Ruth = 路得记
1 Samuel = 撒母耳记上 | 2 Samuel = 撒母耳记下 | 1 Kings = 列王纪上 | 2 Kings = 列王纪下
1 Chronicles = 历代志上 | 2 Chronicles = 历代志下 | Ezra = 以斯拉记 | Nehemiah = 尼希米记
Esther = 以斯帖记 | Job = 约伯记 | Psalms = 诗篇 | Proverbs = 箴言 | Ecclesiastes = 传道书
Song of Songs = 雅歌 | Isaiah = 以赛亚书 | Jeremiah = 耶利米书 | Lamentations = 耶利米哀歌
Ezekiel = 以西结书 | Daniel = 但以理书 | Hosea = 何西阿书 | Joel = 约珥书 | Amos = 阿摩司书
Obadiah = 俄巴底亚书 | Jonah = 约拿书 | Micah = 弥迦书 | Nahum = 那鸿书 | Habakkuk = 哈巴谷书
Zephaniah = 西番雅书 | Haggai = 哈该书 | Zechariah = 撒迦利亚书 | Malachi = 玛拉基书
Matthew = 马太福音 | Mark = 马可福音 | Luke = 路加福音 | John = 约翰福音 | Acts = 使徒行传
Romans = 罗马书 | 1 Corinthians = 哥林多前书 | 2 Corinthians = 哥林多后书
Galatians = 加拉太书 | Ephesians = 以弗所书 | Philippians = 腓立比书 | Colossians = 歌罗西书
1 Thessalonians = 帖撒罗尼迦前书 | 2 Thessalonians = 帖撒罗尼迦后书
1 Timothy = 提摩太前书 | 2 Timothy = 提摩太后书 | Titus = 提多书 | Philemon = 腓利门书
Hebrews = 希伯来书 | James = 雅各书 | 1 Peter = 彼得前书 | 2 Peter = 彼得后书
1 John = 约翰一书 | 2 John = 约翰二书 | 3 John = 约翰三书 | Jude = 犹大书
Revelation = 启示录"""


def base_prompt(cfg):
    target = TARGETS[cfg["target"]]
    heard, said = EXAMPLE[cfg["target"]]
    return f"""You are a simultaneous interpreter for a church sermon.
The speaker is talking in {SPOKEN[cfg["source"]]}. Translate every utterance into {target}.

- Output ONLY the {target} translation of the current utterance: no greetings, no commentary,
  no quotes, no labels, no explanation of your reasoning.
- Translate, never reply. A question stays a question and a command stays a command: you
  answer nothing, agree with nothing, and preach nothing of your own.
- Sentence for sentence. Do not summarise, shorten, expand, or add anything the speaker did
  not say. Preserve the speaker's first person voice and register.
- Keep proper names exact.
- Scripture references: take the book name from the list at the end of these instructions,
  and repeat the chapter and verse the speaker gave, in digits, exactly as spoken. Never add,
  change, complete or guess a chapter or verse number. A book named on its own stays a book
  named on its own.
- Translate the words the speaker actually said, including when they are quoting the Bible.
  Never substitute remembered scripture wording, and never supply verse text they did not say.
- Earlier turns are context for names and terminology only. Never re-translate them and never
  continue your own previous answer.
- If the utterance is a fragment, translate the fragment as it stands; do not finish the thought.
- If an utterance is unintelligible, or is already in {target}, output nothing.

Speaker: {heard}
You: {said}

Bible book names. Any house style in these instructions -- Traditional characters,
a different translation's wording -- still applies on top of this list:
{BIBLE_BOOKS}"""


def load_config():
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        saved = json.loads(CONFIG_PATH.read_text())
        saved["target"] = LEGACY_TARGET.get(saved.get("target"), saved.get("target"))
        for key, value in saved.items():
            if key in DEFAULTS and valid(key, value):
                cfg[key] = value
            elif key in DEFAULTS:
                print(f"!! ignoring {key}={value!r} in config.json", flush=True)
    return cfg


def omni_prompt(cfg):
    """The audio-in model will not take the cascade's prompt, and barely tolerates prose.

    Measured over eight sermon utterances that invite a reply ("Can I get an amen?",
    "Good morning, how are you all doing?"):

    - prose rules make it transcribe the English or emit a bare <|im_start|>, however the
      rule is phrased -- "Translate, never reply" and "never answer the speaker" both fail;
    - the Bible book list made it read the list itself out loud, in full, for short
      utterances: 4 of those 8 were not translated at all;
    - dropping the list and repeating one imperative either side of the glossary translates
      8 of 8, and still gets 6 of 7 book names right on its own -- including 提摩太后书,
      哥林多前书 and 约翰三书, the numbered books the cascade's 8B model got wrong. The
      seventh is 哈巴谷 for "Habakkuk tells us", which is the prophet speaking, and correct.

    So: no list, no rules, the instruction repeated around whatever house style there is.
    """
    imperative = (f"Translate into {TARGETS[cfg['target']]}. "
                  f"Output only the {TARGETS[cfg['target']]} translation.")
    style = house_style()
    return f"{imperative}\n\n{style}{imperative}" if style else imperative


def house_style():
    """The glossary, minus the operator's own notes. Shared by both prompts."""
    raw = GLOSSARY_PATH.read_text() if GLOSSARY_PATH.exists() else ""
    # '#' lines are notes to whoever maintains the file. They must not reach the model, which
    # would otherwise read "copy this to glossary.txt" as part of its instructions.
    glossary = "\n".join(l for l in raw.splitlines() if not l.lstrip().startswith("#")).strip()
    return glossary + "\n\n" if glossary else ""


def instructions(cfg):
    """Rebuilt on every session.update, so glossary and target edits apply without a restart."""
    if cfg.get("engine") == "omni":
        return omni_prompt(cfg)
    glossary = house_style().strip()
    return base_prompt(cfg) + ("\n" + glossary if glossary else "")


class Fanout:
    """One producer, many HTTP listeners. A listener that stops draining is dropped."""

    def __init__(self, maxq):
        self.qs, self.maxq, self.lock = set(), maxq, threading.Lock()

    def publish(self, item):
        with self.lock:
            for q in list(self.qs):
                if q.qsize() > self.maxq:
                    self.qs.discard(q)
                else:
                    q.put(item)

    @contextlib.contextmanager
    def subscribe(self):
        q = queue.Queue()
        with self.lock:
            self.qs.add(q)
        try:
            yield q
        finally:
            with self.lock:
                self.qs.discard(q)

    def count(self):
        with self.lock:
            return len(self.qs)


out_q = queue.Queue()             # translated PCM, chunked
audio = Fanout(MAX_QUEUED)        # mp3 chunks
subs = Fanout(20)                 # subtitle lines
events = Fanout(50)               # operator console updates
recent = []                       # last few lines, so a phone joining mid-sermon sees context


def port_open(port):
    with socket.socket() as s:
        s.settimeout(.4)
        return s.connect_ex(("127.0.0.1", port)) == 0


_ip_cache = (0.0, "127.0.0.1")


def lan_ip():
    """Cached: status() runs twice a second and this opens a socket. A church LAN with no
    route to the internet is normal, so never let this be the thing that breaks the page."""
    global _ip_cache
    at, value = _ip_cache
    if time.monotonic() - at < 30:
        return value
    for attempt in (lambda: _probe_route(), lambda: socket.gethostbyname(socket.gethostname())):
        try:
            found = attempt()
            if found and not found.startswith("127."):
                _ip_cache = (time.monotonic(), found)
                return found
        except OSError:
            continue
    _ip_cache = (time.monotonic(), value)
    return value


def _probe_route():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(.4)
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]


def subtitle(kind, text):
    """kind is "src" (what the preacher said) or "out" (the translation)."""
    if not (text := text.strip()):
        return
    print(("  " if kind == "src" else "  -> ") + text, flush=True)
    line = {"kind": kind, "text": text, "at": time.strftime("%H:%M:%S")}
    recent.append(line)
    del recent[:-8]
    subs.publish(f"data: {json.dumps(line)}\n\n".encode())
    events.publish(("line", line))


class Pipeline:
    """Supervises `speech-to-speech serve` and the WebSocket session against it."""

    def __init__(self):
        self.cfg = load_config()
        self.state = "stopped"      # stopped | starting | running | error
        self.detail = ""
        self.proc = self.ws = self.mic = self.omni = None
        self.level = 0.0            # most recent mic peak, 0..1
        self.last_cb = 0.0          # when the mic last handed us a block
        self.lock = threading.Lock()
        # Three threads open and close the mic now -- the console's thread through update()
        # and rescan(), the heartbeat through check_mic(), and startup -- and PortAudio is
        # torn right down and rebuilt in the middle of a rescan. Reentrant because both
        # check_mic() and rescan() go on to call open_mic().
        self.mic_lock = threading.RLock()

    # ---------------------------------------------------------------- status
    def status(self):
        return {"state": self.state, "detail": self.detail, "config": self.cfg,
                "listeners": audio.count(), "level": round(self.level, 3),
                "url": f"http://{lan_ip()}:{HTTP_PORT}/"}

    def _set(self, state, detail=""):
        self.state, self.detail = state, detail
        print(f"[{state}] {detail}", flush=True)
        events.publish(("status", self.status()))

    # ----------------------------------------------------------- lifecycle
    def start(self):
        with self.lock:
            if self.state in ("starting", "running"):
                return
            self._set("starting", "launching pipeline")
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            if self.cfg["engine"] == "omni":
                self._start_omni()
            if not port_open(S2S_PORT):
                self.proc = subprocess.Popen(self._command(), cwd=HERE)
                size = "35 GB" if "35B" in self.cfg["model"] else "6.6 GB"
                self._set("starting", f"loading models (first run downloads ~{size})")
                deadline = time.monotonic() + LOAD_TIMEOUT_S
                while not port_open(S2S_PORT):
                    if self.proc.poll() is not None:
                        raise RuntimeError(f"pipeline exited with code {self.proc.returncode}")
                    if time.monotonic() > deadline:
                        raise RuntimeError("pipeline did not answer on :%d in time" % S2S_PORT)
                    time.sleep(1)
            else:
                self._set("starting", "attaching to a pipeline already on :%d" % S2S_PORT)

            from websockets.sync.client import connect
            self.ws = connect(f"ws://127.0.0.1:{S2S_PORT}/v1/realtime", max_size=None)
            self.apply_instructions()
            rescan()
            self.open_mic()
            threading.Thread(target=self._read_ws, daemon=True).start()
            self._set("running", "translating")
        except Exception as exc:
            self._set("error", str(exc))
            self.stop()

    def _start_omni(self):
        """The audio-in model runs in its own venv: mlx-vlm pulls a newer mlx than the
        pipeline is pinned to, and that pin is deliberate (utils/mlx_lock.py)."""
        if port_open(OMNI_PORT):
            self._set("starting", "attaching to an audio model already on :%d" % OMNI_PORT)
            return
        if not OMNI_PYTHON.exists():
            raise RuntimeError(f"{OMNI_PYTHON} is missing -- see the README for the one-time "
                               f"setup of the audio model's venv")
        self._set("starting", "loading the audio model (36 GB)")
        self.omni = subprocess.Popen([str(OMNI_PYTHON), "-m", "mlx_vlm.server",
                                      "--model", OMNI_MODEL, "--port", str(OMNI_PORT)],
                                     cwd=HERE, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + LOAD_TIMEOUT_S
        while not port_open(OMNI_PORT):
            if self.omni.poll() is not None:
                raise RuntimeError(f"the audio model exited with code {self.omni.returncode}")
            if time.monotonic() > deadline:
                raise RuntimeError("the audio model did not answer on :%d in time" % OMNI_PORT)
            time.sleep(1)

    def _command(self):
        c = self.cfg
        if c["engine"] == "omni":
            # No STT stage at all: the VAD's audio goes straight to the model, through the
            # proxy above, which is this same HTTP server.
            return ["speech-to-speech", "serve", "--mac-optimal-settings",
                    "--stt", "none", "--llm_backend", "chat-completions",
                    "--model_name", OMNI_MODEL,
                    "--responses_api_base_url", f"http://127.0.0.1:{HTTP_PORT}/omni/v1",
                    "--tts", c["tts"],
                    # No history. Given previous turns the model starts answering the chat
                    # rather than translating it: it prefixes replies with "Assistant:" and,
                    # by the fourth turn, began translating the book list out loud instead of
                    # the sermon. One utterance at a time is what it is good at, and the
                    # prompt asks for sentence-for-sentence anyway. Hence chat_size stays a
                    # cascade-only setting.
                    "--chat_size", "0",
                    "--min_silence_ms", str(c["min_silence_ms"]), "--num_pipelines", "1",
                    "--stream_batch_sentences", "1", "--no_compact_history",
                    # Only this turn's audio. The default keeps recent turns' audio in the
                    # history, re-sending and re-encoding it on every turn.
                    "--responses_api_audio_history_turns", "0",
                    "--no_smart_turn",
                    "--speculative_reopen_ms", "0", "--unanswered_reopen_ms", "0"]
        return ["speech-to-speech", "serve", "--mac-optimal-settings",
                "--stt", c["stt"], "--language", c["source"], "--tts", c["tts"],
                "--model_name", c["model"], "--chat_size", str(c["chat_size"]),
                "--min_silence_ms", str(c["min_silence_ms"]), "--num_pipelines", "1",
                # The voice cannot start until the translator hands it a batch, and a batch
                # is three finished sentences by default. One sentence is what a simultaneous
                # interpreter does anyway, and it is the largest single win in voice delay.
                "--stream_batch_sentences", "1",
                # Otherwise every turn past chat_size fires a background LLM call to summarise
                # the history -- on the same GPU lock the voice is waiting for, to produce a
                # summary this prompt tells the model to ignore. Evict the old turn instead.
                "--no_compact_history",
                # The big one. These three keep a turn open across a pause so a person
                # thinking mid-sentence is not cut off: an uncommitted turn reopens for
                # max(speculative_reopen_ms, unanswered_reopen_ms, smart_turn_max_wait_ms)
                # = 7 s by default. Sermon pauses are shorter than that, so every sentence
                # reopened the same turn and nothing was translated or spoken until the
                # preacher stopped for seven seconds -- and each reopen re-spoke the whole
                # turn from the top. A preacher is not waiting for a reply; commit at the
                # pause. Measured on a 15 s sample: first audio at 4.3 s instead of 15.6 s,
                # and 11.6 s of speech synthesised instead of 28 s.
                "--no_smart_turn",
                "--speculative_reopen_ms", "0", "--unanswered_reopen_ms", "0"]

    def stop(self):
        # Both of these under the lock, and the socket dropped inside it: terminating the
        # pipeline below can take 15 s, and for all of that the state is still "running".
        # check_mic() would see a missing mic and helpfully open a new one, leaving a live
        # stream feeding a closed socket after the pipeline is gone.
        with self.mic_lock:
            if self.mic:
                with contextlib.suppress(Exception):
                    self.mic.close()
                self.mic = None
            self.level = 0.0
            if self.ws:
                with contextlib.suppress(Exception):
                    self.ws.close()
                self.ws = None
        for name in ("proc", "omni"):          # the pipeline, then the audio model behind it
            child = getattr(self, name)
            if child:
                child.terminate()
                with contextlib.suppress(subprocess.TimeoutExpired):
                    child.wait(15)
                if child.poll() is None:
                    child.kill()
                setattr(self, name, None)
        if self.state != "error":
            self._set("stopped", "")

    def restart(self):
        self.stop()
        self.start()

    # -------------------------------------------------------------- session
    def apply_instructions(self):
        self.ws.send(json.dumps({"type": "session.update", "session": {
            "type": "realtime", "instructions": instructions(self.cfg),
            "audio": {"input": {"turn_detection": {"type": "server_vad",
                                                   "interrupt_response": False}}}}}))

    def open_mic(self):
        import sounddevice as sd
        with self.mic_lock:
            self._open_mic()

    def _open_mic(self):
        import sounddevice as sd
        if self.mic:
            with contextlib.suppress(Exception):
                self.mic.close()
            self.mic = None
        want = self.cfg["device"]
        if isinstance(want, str) and not any(d["name"] == want and d["max_input_channels"] > 0
                                             for d in sd.query_devices()):
            # AirPods that wandered off mid-sermon. The built-in mic beats no translation.
            print(f"!! {want} is not connected, using the system default", flush=True)
            want = None

        def cb(indata, frames, t, status):
            self.last_cb = time.monotonic()
            raw = bytes(indata)
            samples = array.array("h", raw)
            self.level = max(abs(min(samples)), abs(max(samples))) / 32768 if samples else 0.0
            with contextlib.suppress(Exception):
                self.ws.send(json.dumps({"type": "input_audio_buffer.append",
                                         "audio": base64.b64encode(raw).decode()}))

        self.mic = sd.RawInputStream(samplerate=RATE, channels=1, dtype="int16",
                                     device=want, blocksize=BLOCK * 4, callback=cb)
        self.mic.start()
        self.last_cb = time.monotonic()      # a fresh stream is not a dead one

    def check_mic(self):
        """AirPods that wander off mid-sermon, or a USB interface someone unplugs, leave the
        stream open and mute: PortAudio simply stops calling back. Nothing else notices --
        the state stays "running", the pipeline sits there translating silence, and the only
        clue is a level meter that never moves. A failed rescan lands here too, having left
        self.mic as None. Silence in the room still produces callbacks, so this only fires
        when the device itself is gone."""
        with self.mic_lock:
            # ws is None for the whole of stop(), which is how this tells a device that died
            # from a pipeline that is being shut down on purpose.
            if self.state != "running" or self.ws is None:
                return
            try:
                alive = self.mic is not None and self.mic.active and \
                    time.monotonic() - self.last_cb < MIC_DEAD_S
            except Exception:
                alive = False                # a stream that raises when asked is not alive
            if alive:
                return
            self.level = 0.0
            try:
                self._open_mic()             # it may be back, or the default will do
                print("!! the microphone stopped; reopened it", flush=True)
            except Exception as exc:
                # state becomes "error", so this cannot loop: the next tick returns above.
                self._set("error", f"the microphone stopped: {exc}")

    def _read_ws(self):
        # The translator and the voice share one GPU lock -- Apple Silicon has one GPU and
        # mlx serialises it -- so a turn's audio arrives in gusts: the TTS stalls every time
        # the language model takes the lock to write the next sentence. Played as it lands,
        # that silence lands inside words. So hold a lead of lead_ms and let the pacer play
        # out of that while the next gust is generated. Holding the whole turn instead is
        # gapless, but costs a whole turn of delay before the first word is heard.
        held = bytearray()
        try:
            for msg in self.ws:
                ev = json.loads(msg)
                t = ev.get("type", "")
                # GA calls it response.output_audio.delta; older builds response.audio.delta
                if t.endswith("audio.delta") and "transcript" not in t:
                    held += base64.b64decode(ev["delta"])
                    # Read per gust, so an operator who hears chopping can raise the lead
                    # mid-sermon and hear the difference on the next sentence.
                    if len(held) >= self.cfg["lead_ms"] * RATE * 2 // 1000:
                        out_q.put(bytes(held))
                        held.clear()
                elif t.endswith("audio.done") and "transcript" not in t:
                    if held:
                        out_q.put(bytes(held))
                        held.clear()
                elif "input_audio_transcription" in t and t.endswith((".completed", ".done")):
                    subtitle("src", ev.get("transcript", ""))
                elif t.endswith("audio_transcript.done"):
                    # text is ready before the audio it narrates, so subtitles run a little ahead
                    subtitle("out", ev.get("transcript", ""))
        except Exception as exc:
            if self.state == "running":
                self._set("error", f"lost the pipeline: {exc}")

    # ---------------------------------------------------------------- config
    def update(self, patch):
        """Returns True when the change needs a pipeline restart to take effect."""
        changed = {k: v for k, v in patch.items()
                   if k in DEFAULTS and v != self.cfg[k] and valid(k, v)}
        self.cfg.update(changed)
        CONFIG_PATH.write_text(json.dumps(self.cfg, indent=2) + "\n")
        if "device" in changed and self.state == "running":
            try:
                self.open_mic()
            except Exception as exc:
                self._set("error", f"cannot open that input: {exc}")
        if "target" in changed and self.state == "running":
            self.apply_instructions()
        events.publish(("status", self.status()))
        return bool(NEEDS_RESTART & set(changed))


pipeline = Pipeline()


def rescan():
    """PortAudio snapshots the audio devices when it starts and never looks again, so a
    headset paired after that is invisible -- and `device: null` keeps resolving to whatever
    was the system default back then. Restarting it is the only rescan PortAudio offers.
    It closes every open stream, hence reopening the mic."""
    import sounddevice as sd
    # Under the lock: for the moment PortAudio is torn down there is no mic, and the
    # heartbeat's check_mic() would otherwise try to open one against a dead PortAudio and
    # put the whole pipeline into an error state over a routine device rescan.
    with pipeline.mic_lock:
        live = pipeline.mic is not None
        if live:
            with contextlib.suppress(Exception):
                pipeline.mic.close()
            pipeline.mic = None
        sd._terminate()
        sd._initialize()
        if live:
            pipeline.open_mic()


def devices():
    """A broken or missing PortAudio is a real macOS failure; say so instead of 500ing."""
    try:
        import sounddevice as sd
        rescan()
        return {"devices": [{"name": d["name"], "channels": d["max_input_channels"]}
                            for d in sd.query_devices()
                            if d["max_input_channels"] > 0], "error": ""}
    except Exception as exc:
        return {"devices": [], "error": f"Cannot read audio devices: {exc}"}


# The pipeline wraps our instructions in a voice-assistant envelope: a lead about being in a
# spoken conversation, then "Session Prompt:", then a "## Voice Rules" tail telling it to keep
# replies brief and treat transcripts as noisy. All of that is prose about being an assistant,
# and prose is what makes the audio-in model transcribe the English instead of translating it.
# Keep the session prompt, drop the envelope. If upstream renames these markers we fall back
# to passing the whole thing through, which is what we did before.
SESSION_PROMPT = re.compile(r"Session Prompt:\n(.*?)(?:\n#+ Voice Rules|\Z)", re.S)


def session_prompt_of(system_message):
    """Our instructions, unwrapped from whatever the pipeline put around them."""
    found = SESSION_PROMPT.search(system_message)
    return found.group(1).strip() if found else system_message


TEMPLATE_TOKEN = re.compile(rb"<\|[a-z_]+\|>")


def sanitize_template_tokens(chunk):
    """Belt and braces. The prompt shape above avoids the leak, but a chat-template token
    reaching the subtitles would also be read out loud by the voice, so strip any that do."""
    return TEMPLATE_TOKEN.sub(b"", chunk)


def broadcast_died(why):
    """Both encoder threads are daemons: without this the stream just stops, forever,
    with nothing on screen to say so."""
    print(f"!! {why}", flush=True)
    if pipeline.state == "running":
        pipeline._set("error", why)


def pacer(stdin, stop=None):
    """Feed ffmpeg at wall-clock rate: translated audio when we have it, silence otherwise."""
    # pos walks through buf rather than reslicing it: buf now holds a whole turn, and
    # copying a quarter of a megabyte fifty times a second is not what this thread is for.
    silence, buf, pos = b"\0" * (BLOCK * 2), b"", 0
    t0, n = time.monotonic(), 0
    while not (stop and stop.is_set()):
        while len(buf) - pos < BLOCK * 2 and not out_q.empty():
            buf, pos = buf[pos:] + out_q.get_nowait(), 0
        # ponytail: drop oldest on backlog. A faster speaker than the TTS drifts forever
        # otherwise. Upgrade path if this fires often: shrink chat_size, faster TTS.
        # In bytes, not items: a queued item is lead_ms of speech, or the tail of a turn.
        with out_q.mutex:
            queued = sum(len(chunk) for chunk in out_q.queue)
        if queued + len(buf) - pos > MAX_LAG_S * RATE * 2:
            print("!! backlog, dropping audio", flush=True)
            with out_q.mutex:
                out_q.queue.clear()
        if len(buf) - pos >= BLOCK * 2:
            chunk, pos = buf[pos:pos + BLOCK * 2], pos + BLOCK * 2
        else:
            chunk = silence
        try:
            stdin.write(chunk)
            stdin.flush()
        except OSError as exc:
            return broadcast_died(f"the audio encoder went away: {exc}")
        n += 1
        # E. After a sleep or a long stall the schedule is far in the past; catching up would
        # dump a burst of audio at listeners. Start the clock again instead.
        behind = time.monotonic() - (t0 + n * BLOCK / RATE)
        if behind > 2:
            t0, n = time.monotonic(), 0
        else:
            time.sleep(max(0, -behind))


def fanout(stdout):
    while chunk := stdout.read(MP3_CHUNK):
        audio.publish(chunk)
    broadcast_died("the audio encoder stopped producing output")


def heartbeat():
    """Keeps the console's meters moving without the pipeline having to push them."""
    while True:
        time.sleep(.5)
        try:
            pipeline.check_mic()      # not gated on a console being open: the mic dying
            if events.count():        # is worth noticing whether anyone is watching or not
                events.publish(("status", pipeline.status()))
        except Exception as exc:
            # This thread is the mic watchdog as well as the meters. If it dies the console
            # freezes and a mic that goes away is never noticed again, both in silence.
            print(f"!! heartbeat: {exc}", flush=True)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # ponytail: the console is localhost-only, so nobody on the church wifi can stop the
    # broadcast. If an operator ever needs it from a tablet, add a token to the URL.
    def local_only(self):
        if self.client_address[0] not in ("127.0.0.1", "::1"):
            self.send_error(403, "The console is only available on this Mac")
            return False
        return True

    def send_json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, name, ctype):
        body = (HERE / name).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        # The console is read fresh from disk on every request, but a browser that cached an
        # older copy will happily show it for the rest of the day -- so a control added by an
        # update is simply missing, with nothing to say why.
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def frame(item):
        """Console events arrive as (name, payload); everything else is already bytes."""
        if isinstance(item, tuple):
            name, data = item
            return f"event: {name}\ndata: {json.dumps(data)}\n\n".encode()
        return item

    def stream(self, fanout_, content_type, backlog=()):
        self.send_response(200)
        for k, v in [("Content-Type", content_type), ("Cache-Control", "no-cache"),
                     ("Connection", "close")]:
            self.send_header(k, v)
        self.end_headers()
        with fanout_.subscribe() as q:
            try:
                for item in backlog:
                    self.wfile.write(self.frame(item))
                while True:
                    try:
                        self.wfile.write(self.frame(q.get(timeout=15)))
                    except queue.Empty:
                        # An SSE comment keeps a dozing phone's socket open. It must never go
                        # down /stream.mp3, where those bytes land inside an audio frame.
                        if content_type.startswith("text/"):
                            self.wfile.write(b":\n\n")
            except Exception:
                pass

    def do_GET(self):
        path = self.path.split("?")[0]
        if path.startswith("/stream.mp3"):
            self.stream(audio, "audio/mpeg")
        elif path == "/subs":
            self.stream(subs, "text/event-stream",
                        [f"data: {json.dumps(l)}\n\n".encode() for l in list(recent)])
        elif path == "/admin":
            if self.local_only():
                self.send_file("admin.html", "text/html; charset=utf-8")
        elif path == "/api/events":
            if self.local_only():
                self.stream(events, "text/event-stream",
                            [("status", pipeline.status())] + [("line", l) for l in list(recent)])
        elif path == "/api/glossary":
            if self.local_only():
                self.send_json({"text": GLOSSARY_PATH.read_text()
                                if GLOSSARY_PATH.exists() else ""})
        elif path == "/api/glossary/example":
            if self.local_only():
                self.send_file("glossary.example.txt", "text/plain; charset=utf-8")
        elif path == "/api/devices":
            if self.local_only():
                self.send_json(devices())
        elif path == "/favicon.svg":
            self.send_file("favicon.svg", "image/svg+xml")
        elif path == "/qr.svg":
            try:
                import segno
                # omitsize gives a viewBox and no width/height: without it the svg carries
                # a fixed 198px and the console's max-width clips it instead of scaling it,
                # which is what pushed the code off-centre in its white box.
                body = segno.make(pipeline.status()["url"]).svg_inline(
                    scale=6, omitsize=True).encode()
            except ImportError:
                return self.send_error(404, "pip install segno for a QR code")
            self.send_response(200)
            self.send_header("Content-Type", "image/svg+xml")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_file("index.html", "text/html; charset=utf-8")

    def omni_proxy(self, body):
        """Sit between the pipeline and the audio-in model's server.

        The pipeline delivers its instructions as a system message, and mlx-vlm's server
        mangles those for this model: the reply comes back as chat-template tokens and a
        truncated answer ('<|im_start|>user\\nJohn three'). The same text works when it rides
        in the user turn, after the audio -- before the audio and it transcribes instead of
        translating. So move it, forward, and stream the answer back."""
        try:
            req = json.loads(body)
        except ValueError as exc:
            return self.send_error(400, f"bad proxy body: {exc}")

        messages, carried = [], []
        for m in req.get("messages", []):
            if m.get("role") == "system":
                carried.append(session_prompt_of(m.get("content") or ""))
            else:
                messages.append(m)
        if carried and messages:
            last = dict(messages[-1])
            content = last.get("content")
            if not isinstance(content, list):
                content = [{"type": "text", "text": str(content or "")}]
            # after the audio: the order is what decides translate vs transcribe
            last["content"] = list(content) + [{"type": "text", "text": "\n".join(carried)}]
            messages[-1] = last
        req["messages"] = messages

        upstream = urllib.request.Request(
            f"http://127.0.0.1:{OMNI_PORT}/v1/chat/completions",
            data=json.dumps(req).encode(), headers={"Content-Type": "application/json"})
        try:
            r = urllib.request.urlopen(upstream, timeout=600)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            print(f"!! the audio model rejected the request: {exc.code} {detail}", flush=True)
            return self.send_error(502, f"the audio model rejected the request: {exc.code}")
        except urllib.error.URLError as exc:
            print(f"!! the audio model is not answering: {exc}", flush=True)
            return self.send_error(502, f"the audio model is not answering: {exc}")

        self.send_response(200)
        self.send_header("Content-Type", r.headers.get("Content-Type", "application/json"))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        while chunk := r.read(4096):
            with contextlib.suppress(OSError):
                self.wfile.write(sanitize_template_tokens(chunk))
                self.wfile.flush()

    def do_POST(self):
        if not self.local_only():
            return
        length = int(self.headers.get("Content-Length") or 0)
        # A turn of audio arrives here base64'd -- 20 s of it is about 850 KB -- so the proxy
        # needs far more headroom than a console form post.
        cap = 64 << 20 if self.path.startswith("/omni/") else 1 << 20
        if length > cap:
            return self.send_error(413, "too large")
        try:
            body = self.rfile.read(length)
            data = json.loads(body) if body else {}
        except (ValueError, OSError) as exc:
            return self.send_error(400, f"bad request body: {exc}")
        path = self.path.split("?")[0]
        if path == "/omni/v1/chat/completions":
            return self.omni_proxy(body)
        if path == "/api/start":
            pipeline.start()
        elif path == "/api/stop":
            pipeline.stop()
        elif path == "/api/restart":
            threading.Thread(target=pipeline.restart, daemon=True).start()
        elif path == "/api/config":
            return self.send_json({"restart_required": pipeline.update(data)})
        elif path == "/api/glossary":
            GLOSSARY_PATH.write_text(data.get("text", ""))
            if pipeline.state == "running":
                pipeline.apply_instructions()          # applies without a restart
        else:
            return self.send_error(404)
        self.send_json(pipeline.status())

    def log_message(self, *a):
        pass


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--no-start", action="store_true",
                    help="serve the console but wait for it to start the pipeline")
    args = ap.parse_args()

    ff = subprocess.Popen(
        ["ffmpeg", "-loglevel", "error", "-f", "s16le", "-ar", str(RATE), "-ac", "1",
         # The bit reservoir lets a frame borrow space from later ones, so the encoder sits
         # on finished audio waiting to see what comes next. Worth it for music, not for a
         # live voice: it held back a third of a second of speech, permanently.
         "-i", "pipe:0", "-c:a", "libmp3lame", "-b:a", f"{BITRATE // 1000}k", "-reservoir", "0",
         "-f", "mp3", "pipe:1"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    for fn, a in ((pacer, (ff.stdin,)), (fanout, (ff.stdout,)), (heartbeat, ())):
        threading.Thread(target=fn, args=a, daemon=True).start()

    # The default action for both of these kills the interpreter outright, skipping the
    # cleanup below and orphaning ffmpeg and the pipeline. launchd sends TERM; closing the
    # Terminal window the launcher opened sends HUP.
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, lambda *_: sys.exit(0))

    server = ThreadingHTTPServer(("0.0.0.0", HTTP_PORT), Handler)
    url = pipeline.status()["url"]
    print(f"\n  Listeners: {url}\n  Console:   http://localhost:{HTTP_PORT}/admin\n")
    with contextlib.suppress(ImportError):
        import segno
        segno.make(url).terminal(compact=True)
    if not args.no_start:
        pipeline.start()
    try:
        server.serve_forever()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        pipeline.stop()
        ff.terminate()


if __name__ == "__main__":
    sys.exit(main())
