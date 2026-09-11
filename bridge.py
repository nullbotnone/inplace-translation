#!/usr/bin/env python3
"""Sermon translator bridge.

mic -> speech-to-speech realtime server (VAD/STT/LLM/TTS) -> ffmpeg mp3 -> LAN listeners.

Run `speech-to-speech serve` first, then this. Listeners open http://<lan-ip>:8000/
"""
import argparse, base64, contextlib, json, queue, socket, subprocess, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

S2S_URL = "ws://127.0.0.1:8765/v1/realtime"
HTTP_PORT = 8000
RATE, BLOCK = 16000, 320          # 20 ms of s16le mono
MAX_LAG_S = 20                    # translation backlog before we start dropping
MAX_QUEUED = 60                   # mp3 chunks buffered per listener (~10 s) before eviction
GLOSSARY = Path("glossary.txt").read_text().strip() if Path("glossary.txt").exists() else ""

INSTRUCTIONS = f"""You are a simultaneous interpreter for a church sermon.
Translate every utterance between English and Chinese: English input -> Simplified Chinese
output, Chinese input -> English output. Output ONLY the translation, nothing else: no
greetings, no commentary, no quotes, no explanation of your reasoning. Preserve the
speaker's first person voice and register. Keep Bible book/chapter/verse references and
proper names exact. If an utterance is unintelligible, output nothing.
{GLOSSARY}"""

out_q = queue.Queue()             # translated PCM, chunked


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


audio = Fanout(MAX_QUEUED)        # mp3 chunks
subs = Fanout(20)                 # subtitle lines
recent = []                       # last few lines, so a phone joining mid-sermon sees context


def mic_to_server(ws, device=None):
    import sounddevice as sd

    def cb(indata, frames, t, status):
        ws.send(json.dumps({"type": "input_audio_buffer.append",
                            "audio": base64.b64encode(bytes(indata)).decode()}))
    with sd.RawInputStream(samplerate=RATE, channels=1, dtype="int16", device=device,
                           blocksize=BLOCK * 4, callback=cb):
        threading.Event().wait()


def subtitle(kind, text):
    """kind is "src" (what the preacher said) or "out" (the translation)."""
    if not (text := text.strip()):
        return
    print(("  " if kind == "src" else "  -> ") + text, flush=True)
    line = json.dumps({"kind": kind, "text": text})
    recent.append(line)
    del recent[:-8]
    subs.publish(f"data: {line}\n\n".encode())


def server_to_pcm(ws):
    for msg in ws:
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


def pacer(stdin, stop=None):
    """Feed ffmpeg at wall-clock rate: translated audio when we have it, silence otherwise."""
    silence, buf = b"\0" * (BLOCK * 2), b""
    t0, n = time.monotonic(), 0
    while not (stop and stop.is_set()):
        while len(buf) < BLOCK * 2 and not out_q.empty():
            buf += out_q.get_nowait()
        # ponytail: drop oldest on backlog. A faster speaker than the TTS drifts forever
        # otherwise. Upgrade path if this fires often: shrink --chat_size, faster TTS.
        if out_q.qsize() * BLOCK * 2 > MAX_LAG_S * RATE * 2:
            print("!! backlog, dropping audio", flush=True)
            with out_q.mutex:
                out_q.queue.clear()
        chunk, buf = (buf[:BLOCK * 2], buf[BLOCK * 2:]) if len(buf) >= BLOCK * 2 else (silence, buf)
        stdin.write(chunk)
        stdin.flush()
        n += 1
        time.sleep(max(0, t0 + n * BLOCK / RATE - time.monotonic()))


def fanout(stdout):
    while chunk := stdout.read(1024):
        audio.publish(chunk)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def stream(self, fanout_, content_type, backlog=()):
        self.send_response(200)
        for k, v in [("Content-Type", content_type), ("Cache-Control", "no-cache"),
                     ("Connection", "close")]:
            self.send_header(k, v)
        self.end_headers()
        with fanout_.subscribe() as q:
            try:
                for item in backlog:
                    self.wfile.write(item)
                while True:
                    try:
                        self.wfile.write(q.get(timeout=15))
                    except queue.Empty:
                        self.wfile.write(b":\n\n")   # keep a dozing phone's socket open
            except Exception:
                pass

    def do_GET(self):
        if self.path.startswith("/stream.mp3"):
            self.stream(audio, "audio/mpeg")
        elif self.path == "/subs":
            self.stream(subs, "text/event-stream",
                        [f"data: {l}\n\n".encode() for l in list(recent)])
        else:
            body = Path("index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, *a):
        pass


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(("8.8.8.8", 80))
    ip = s.getsockname()[0]
    s.close()
    return ip


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--device", help="input device name or index "
                                     "(list them with: python3 -m sounddevice)")
    args = ap.parse_args()

    ff = subprocess.Popen(
        ["ffmpeg", "-loglevel", "error", "-f", "s16le", "-ar", str(RATE), "-ac", "1",
         "-i", "pipe:0", "-c:a", "libmp3lame", "-b:a", "48k", "-f", "mp3", "pipe:1"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    from websockets.sync.client import connect
    ws = connect(S2S_URL, max_size=None)
    ws.send(json.dumps({"type": "session.update", "session": {
        "type": "realtime", "instructions": INSTRUCTIONS,
        "audio": {"input": {"turn_detection": {"type": "server_vad",
                                               "interrupt_response": False}}}}}))

    for fn, a in ((server_to_pcm, (ws,)), (mic_to_server, (ws, args.device)),
                  (pacer, (ff.stdin,)), (fanout, (ff.stdout,))):
        threading.Thread(target=fn, args=a, daemon=True).start()

    url = f"http://{lan_ip()}:{HTTP_PORT}/"
    print(f"\n  Listeners: {url}\n")
    try:
        import segno
        segno.make(url).terminal(compact=True)
    except ImportError:
        print("  (pip install segno for a QR code here)")
    ThreadingHTTPServer(("0.0.0.0", HTTP_PORT), Handler).serve_forever()


if __name__ == "__main__":
    sys.exit(main())
