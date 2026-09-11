#!/usr/bin/env python3
"""Sermon translator bridge.

Owns the whole thing: starts the speech-to-speech pipeline, feeds it the mic,
broadcasts the translated audio and subtitles to phones on the LAN, and serves
an operator console at http://localhost:8000/admin

    python3 bridge.py
"""
import argparse, array, base64, contextlib, json, queue, signal, socket, subprocess, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
S2S_PORT, HTTP_PORT = 8765, 8000
RATE, BLOCK = 16000, 320          # 20 ms of s16le mono
MAX_LAG_S = 20                    # translation backlog before we start dropping
MAX_QUEUED = 60                   # mp3 chunks buffered per listener (~10 s) before eviction
LOAD_TIMEOUT_S = 900              # first run downloads ~8 GB before the port answers

CONFIG_PATH = HERE / "config.json"
GLOSSARY_PATH = HERE / "glossary.txt"
DEFAULTS = {
    "device": None,                                              # mic; None = system default
    "source": "en",                                              # what the preacher speaks
    "target": "zh",                                              # what listeners hear
    "model": "mlx-community/Qwen3-4B-Instruct-2507-4bit",
    "stt": "mlx-audio-whisper",
    "tts": "qwen3",
    "chat_size": 2,
    "min_silence_ms": 64,
}
NEEDS_RESTART = {"model", "stt", "tts", "chat_size", "min_silence_ms", "source"}
# device, target and the glossary apply live: the first reopens the mic, the other two only
# change the prompt. "source" sets the recognition language, which is a CLI flag.

SPOKEN = {"en": "English", "zh": "Chinese", "auto": "whatever language the speaker uses"}
TARGETS = {"en": "English", "zh": "Chinese"}
# Listeners hear audio, where 简体 vs 繁體 does not exist. It only shows up in the
# subtitles, so a church that wants Traditional asks for it in the glossary instead.
LEGACY_TARGET = {"zh-Hans": "zh", "zh-Hant": "zh"}

# The console posts these, and a hand-edited config.json can hold anything. An unknown
# value here would reach a CLI flag or a dict lookup, so reject it at the door.
CHOICES = {"source": set(SPOKEN), "target": set(TARGETS), "tts": {"qwen3", "kokoro"}}
BOUNDS = {"chat_size": (0, 8), "min_silence_ms": (32, 2000)}


def valid(key, value):
    if key in CHOICES:
        return value in CHOICES[key]
    if key in BOUNDS:
        lo, hi = BOUNDS[key]
        return isinstance(value, int) and not isinstance(value, bool) and lo <= value <= hi
    if key == "device":
        return value is None or (isinstance(value, int) and value >= 0)
    return isinstance(value, str) and value.strip() != ""

def base_prompt(cfg):
    target = TARGETS[cfg["target"]]
    return f"""You are a simultaneous interpreter for a church sermon.
The speaker is talking in {SPOKEN[cfg["source"]]}. Translate every utterance into {target}.
Output ONLY the {target} translation, nothing else: no greetings, no commentary, no quotes,
no explanation of your reasoning. Preserve the speaker's first person voice and register.
Keep Bible book/chapter/verse references and proper names exact.
If an utterance is unintelligible, or is already in {target}, output nothing."""


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


def instructions(cfg):
    """Rebuilt on every session.update, so glossary and target edits apply without a restart."""
    raw = GLOSSARY_PATH.read_text() if GLOSSARY_PATH.exists() else ""
    # '#' lines are notes to whoever maintains the file. They must not reach the model, which
    # would otherwise read "copy this to glossary.txt" as part of its instructions.
    glossary = "\n".join(l for l in raw.splitlines() if not l.lstrip().startswith("#")).strip()
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
        self.proc = self.ws = self.mic = None
        self.level = 0.0            # most recent mic peak, 0..1
        self.lock = threading.Lock()

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
            if not port_open(S2S_PORT):
                self.proc = subprocess.Popen(self._command(), cwd=HERE)
                self._set("starting", "loading models (first run downloads ~8 GB)")
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
            self.open_mic()
            threading.Thread(target=self._read_ws, daemon=True).start()
            self._set("running", "translating")
        except Exception as exc:
            self._set("error", str(exc))
            self.stop()

    def _command(self):
        c = self.cfg
        return ["speech-to-speech", "serve", "--mac-optimal-settings",
                "--stt", c["stt"], "--language", c["source"], "--tts", c["tts"],
                "--model_name", c["model"], "--chat_size", str(c["chat_size"]),
                "--min_silence_ms", str(c["min_silence_ms"]), "--num_pipelines", "1"]

    def stop(self):
        if self.mic:
            with contextlib.suppress(Exception):
                self.mic.close()
            self.mic = None
        self.level = 0.0
        if self.ws:
            with contextlib.suppress(Exception):
                self.ws.close()
            self.ws = None
        if self.proc:
            self.proc.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                self.proc.wait(15)
            if self.proc.poll() is None:
                self.proc.kill()
            self.proc = None
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
        if self.mic:
            with contextlib.suppress(Exception):
                self.mic.close()

        def cb(indata, frames, t, status):
            raw = bytes(indata)
            samples = array.array("h", raw)
            self.level = max(abs(min(samples)), abs(max(samples))) / 32768 if samples else 0.0
            with contextlib.suppress(Exception):
                self.ws.send(json.dumps({"type": "input_audio_buffer.append",
                                         "audio": base64.b64encode(raw).decode()}))

        self.mic = sd.RawInputStream(samplerate=RATE, channels=1, dtype="int16",
                                     device=self.cfg["device"], blocksize=BLOCK * 4, callback=cb)
        self.mic.start()

    def _read_ws(self):
        try:
            for msg in self.ws:
                ev = json.loads(msg)
                t = ev.get("type", "")
                # GA calls it response.output_audio.delta; older builds response.audio.delta
                if t.endswith("audio.delta") and "transcript" not in t:
                    out_q.put(base64.b64decode(ev["delta"]))
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


def devices():
    """A broken or missing PortAudio is a real macOS failure; say so instead of 500ing."""
    try:
        import sounddevice as sd
        return {"devices": [{"index": i, "name": d["name"], "channels": d["max_input_channels"]}
                            for i, d in enumerate(sd.query_devices())
                            if d["max_input_channels"] > 0], "error": ""}
    except Exception as exc:
        return {"devices": [], "error": f"Cannot read audio devices: {exc}"}


def broadcast_died(why):
    """Both encoder threads are daemons: without this the stream just stops, forever,
    with nothing on screen to say so."""
    print(f"!! {why}", flush=True)
    if pipeline.state == "running":
        pipeline._set("error", why)


def pacer(stdin, stop=None):
    """Feed ffmpeg at wall-clock rate: translated audio when we have it, silence otherwise."""
    silence, buf = b"\0" * (BLOCK * 2), b""
    t0, n = time.monotonic(), 0
    while not (stop and stop.is_set()):
        while len(buf) < BLOCK * 2 and not out_q.empty():
            buf += out_q.get_nowait()
        # ponytail: drop oldest on backlog. A faster speaker than the TTS drifts forever
        # otherwise. Upgrade path if this fires often: shrink chat_size, faster TTS.
        if out_q.qsize() * BLOCK * 2 > MAX_LAG_S * RATE * 2:
            print("!! backlog, dropping audio", flush=True)
            with out_q.mutex:
                out_q.queue.clear()
        chunk, buf = (buf[:BLOCK * 2], buf[BLOCK * 2:]) if len(buf) >= BLOCK * 2 else (silence, buf)
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
    while chunk := stdout.read(1024):
        audio.publish(chunk)
    broadcast_died("the audio encoder stopped producing output")


def heartbeat():
    """Keeps the console's meters moving without the pipeline having to push them."""
    while True:
        time.sleep(.5)
        if events.count():
            events.publish(("status", pipeline.status()))


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
                        self.wfile.write(b":\n\n")      # keep a dozing phone's socket open
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
                body = segno.make(pipeline.status()["url"]).svg_inline(scale=6).encode()
            except ImportError:
                return self.send_error(404, "pip install segno for a QR code")
            self.send_response(200)
            self.send_header("Content-Type", "image/svg+xml")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_file("index.html", "text/html; charset=utf-8")

    def do_POST(self):
        if not self.local_only():
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length > 1 << 20:
            return self.send_error(413, "too large")
        try:
            body = self.rfile.read(length)
            data = json.loads(body) if body else {}
        except (ValueError, OSError) as exc:
            return self.send_error(400, f"bad request body: {exc}")
        path = self.path.split("?")[0]
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
         "-i", "pipe:0", "-c:a", "libmp3lame", "-b:a", "48k", "-f", "mp3", "pipe:1"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    for fn, a in ((pacer, (ff.stdin,)), (fanout, (ff.stdout,)), (heartbeat, ())):
        threading.Thread(target=fn, args=a, daemon=True).start()

    # Default SIGTERM kills the interpreter outright, skipping the cleanup below and
    # orphaning ffmpeg and the pipeline. launchd and the app bundle both send TERM.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

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
