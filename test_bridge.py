"""Self-check: python3 test_bridge.py"""
import base64, json, queue, signal, socket, sys, tempfile, threading, time
from pathlib import Path

import bridge

b64 = lambda raw: base64.b64encode(raw).decode()

tmp = Path(tempfile.mkdtemp())
bridge.CONFIG_PATH = tmp / "config.json"
bridge.GLOSSARY_PATH = tmp / "glossary.txt"


class Sink:
    def __init__(self): self.w = []
    def write(self, b): self.w.append(b)
    def flush(self): pass


def run_pacer(seconds=0.3):
    stop = threading.Event()
    s = Sink()
    t = threading.Thread(target=bridge.pacer, args=(s, stop), daemon=True)
    t.start(); time.sleep(seconds); stop.set(); t.join(1)
    return s.w


# a turn's audio is held until the TTS says it is done, then queued as one piece: played
# gust by gust, the stalls where the LLM has the GPU land as silence inside words
p_ws = bridge.Pipeline()
p_ws.ws = [json.dumps(e) for e in (
    {"type": "response.output_audio.delta", "delta": b64(b"\1\2")},
    {"type": "response.output_audio_transcript.done", "transcript": "hi"},
    {"type": "response.output_audio.delta", "delta": b64(b"\3\4")},
    {"type": "response.output_audio.done"},
    {"type": "response.output_audio.delta", "delta": b64(b"\5\6")},
)]
p_ws._read_ws()
assert bridge.out_q.get_nowait() == b"\1\2\3\4", "a turn should arrive whole, not gust by gust"
assert bridge.out_q.empty(), "audio for an unfinished turn should still be held"

# ... but only up to the lead: a long turn starts playing while the rest is still being
# spoken, instead of the listener waiting out the whole turn first
lead = bridge.DEFAULTS["lead_ms"] * bridge.RATE * 2 // 1000
p_ws.ws = [json.dumps({"type": "response.output_audio.delta", "delta": b64(b"\0" * lead)})]
p_ws._read_ws()
assert bridge.out_q.get_nowait() == b"\0" * lead, "a full lead was not released without done"
assert bridge.out_q.empty()

# the lead is read per gust, so raising it mid-sermon applies to the next sentence
p_ws.cfg = {**bridge.DEFAULTS, "lead_ms": bridge.BOUNDS["lead_ms"][1]}
p_ws.ws = [json.dumps({"type": "response.output_audio.delta", "delta": b64(b"\0" * lead)})]
p_ws._read_ws()
assert bridge.out_q.empty(), "the old lead was still in force"
p_ws.cfg = dict(bridge.DEFAULTS)
with bridge.out_q.mutex:
    bridge.out_q.queue.clear()

# silence when idle, at wall-clock rate (~50 blocks/s), not as fast as the CPU allows
w = run_pacer()
assert 10 < len(w) < 25, f"pacing off: {len(w)} blocks in 0.3 s"
assert set(b"".join(w)) == {0}, "expected silence while idle"

# queued audio comes out ahead of silence, intact and in order
bridge.out_q.put(bytes(range(256)) * 5)          # 1280 B = 2 blocks
assert b"".join(run_pacer(0.1)).startswith(bytes(range(256)) * 5), "queued audio not emitted first"

# backlog past MAX_LAG_S is dropped rather than drifting forever
for _ in range(int(bridge.MAX_LAG_S * bridge.RATE * 2 / 640) + 10):
    bridge.out_q.put(b"\1" * 640)
run_pacer(0.1)
assert bridge.out_q.qsize() < 10, f"backlog not dropped: {bridge.out_q.qsize()}"

# a listener that stops draining is evicted instead of growing without bound
f = bridge.Fanout(maxq=3)
with f.subscribe() as fast, f.subscribe() as slow:
    for _ in range(6):
        f.publish(b"x")
        fast.get()
    assert slow not in f.qs, "lagging listener was not dropped"
    assert fast in f.qs, "keeping-up listener was dropped"
    assert f.count() == 1, "count() disagrees with the live set"
assert not f.qs, "subscribe() did not clean up on exit"

# subtitles reach phones and the console, and a late phone gets the recent backlog
bridge.recent.clear()
with bridge.subs.subscribe() as phone, bridge.events.subscribe() as console:
    bridge.subtitle("src", " For God so loved the world ")
    bridge.subtitle("out", "神爱世人")
    bridge.subtitle("out", "   ")                        # blank turns are not broadcast
    first = json.loads(phone.get_nowait().decode().removeprefix("data: "))
    assert first == {"kind": "src", "text": "For God so loved the world", "at": first["at"]}
    assert json.loads(phone.get_nowait().decode().removeprefix("data: "))["text"] == "神爱世人"
    assert phone.empty(), "empty subtitle was broadcast"
    assert [n for n, _ in [console.get_nowait(), console.get_nowait()]] == ["line", "line"]
assert len(bridge.recent) == 2, "backlog not kept"
for _ in range(20):
    bridge.subtitle("out", "x")
assert len(bridge.recent) == 8, "backlog not capped"

# the prompt names the actual direction, so the model is not left guessing
en2zh = {**bridge.DEFAULTS, "source": "en", "target": "zh"}
zh2en = {**bridge.DEFAULTS, "source": "zh", "target": "en"}
assert "talking in English" in bridge.instructions(en2zh)
assert "into Chinese" in bridge.instructions(en2zh)
assert "talking in Chinese" in bridge.instructions(zh2en)
assert "into English" in bridge.instructions(zh2en)
# the worked example is the text the model borrows from, so it must not carry a scripture
# reference: an example naming "John 3" is what turned a bare 约翰福音 into "John 3"
for cfg in (en2zh, zh2en):
    heard, said = bridge.EXAMPLE[cfg["target"]]
    assert not any(c.isdigit() for c in heard + said), f"a number in the example: {heard!r}"
    assert not any(b.split(" = ")[0] in heard + said or b.split(" = ")[1] in heard + said
                   for line in bridge.BIBLE_BOOKS.splitlines() for b in line.split(" | ")), \
        "the example names a book of the Bible"

# every book name is pinned, both directions, so none of them is left to a 4B model's memory
books = [b for line in bridge.BIBLE_BOOKS.splitlines() for b in line.split(" | ")]
assert len(books) == 66, f"{len(books)} books, expected 66"
assert all(len(b.split(" = ")) == 2 for b in books), "a book line is not 'English = 中文'"
assert len({b.split(" = ")[0] for b in books}) == 66, "a duplicate English book name"
assert len({b.split(" = ")[1] for b in books}) == 66, "a duplicate Chinese book name"
for cfg in (en2zh, zh2en):
    prompt = bridge.instructions(cfg)
    assert "John = 约翰福音" in prompt and "Habakkuk = 哈巴谷书" in prompt, "book list missing"
    assert "guess a chapter or verse" in prompt, "nothing forbids inventing a verse number"

# 简体/繁體 is a written distinction listeners cannot hear, so it is not a target;
# a church that reads Traditional asks for it in the glossary, which still reaches the prompt
bridge.GLOSSARY_PATH.write_text("Write all Chinese in Traditional characters (繁體).")
assert "繁體" in bridge.instructions(en2zh)
bridge.GLOSSARY_PATH.unlink()

# the glossary is read fresh each time, so edits apply without a restart
assert "Grace Chapel" not in bridge.instructions(en2zh)
bridge.GLOSSARY_PATH.write_text("  Grace Chapel -> 恩典堂  ")
assert "Grace Chapel -> 恩典堂" in bridge.instructions(en2zh), "glossary edit not picked up"

# '#' lines are notes to the operator and must never become instructions to the model
bridge.GLOSSARY_PATH.write_text(
    "# Copy this file and edit it\n"
    "  # indented comments count too\n"
    "elder -> 长老\n"
    "not#a#comment -> ok\n")
prompt = bridge.instructions(en2zh)
assert "Copy this file" not in prompt and "indented comments" not in prompt
assert "elder -> 长老" in prompt and "not#a#comment -> ok" in prompt

# the shipped example is safe to paste in whole: nothing in it addresses the reader
example = (Path(__file__).parent / "glossary.example.txt")
bridge.GLOSSARY_PATH.write_text(example.read_text())
prompt = bridge.instructions(en2zh)
assert "#" not in prompt.split(bridge.base_prompt(en2zh))[-1], "a comment survived"
assert "团契" in prompt, "the example contributed nothing"

# the spoken language reaches the recognition flag, not just the prompt
p0 = bridge.Pipeline()
p0.cfg = dict(zh2en)
assert "--language" in p0._command()
assert p0._command()[p0._command().index("--language") + 1] == "zh"
p0.cfg = {**zh2en, "source": "auto"}
assert p0._command()[p0._command().index("--language") + 1] == "auto"

# the voice waits on the translator for its text and on the same GPU lock to speak it, so
# neither may be made to wait longer than it has to: one sentence per batch, and no
# background summary generation competing for the lock mid-sermon
cmd = p0._command()
assert cmd[cmd.index("--stream_batch_sentences") + 1] == "1", "the voice waits on 3 sentences"
assert "--no_compact_history" in cmd, "a background LLM call still contends for the GPU"

# and a turn has to commit at the pause. Left to reopen, a sermon's pauses are all shorter
# than the 7 s reopen window, so nothing is spoken until the preacher stops for good
assert "--no_smart_turn" in cmd, "smart turn holds the turn open for a pausing speaker"
assert cmd[cmd.index("--speculative_reopen_ms") + 1] == "0", "the turn still reopens"
assert cmd[cmd.index("--unanswered_reopen_ms") + 1] == "0", "the turn still reopens"

# junk never reaches a CLI flag or a dict lookup
p1 = bridge.Pipeline()
for bad in ({"source": "klingon"}, {"target": "zh-Hanzi"}, {"tts": "; rm -rf /"},
            {"target": "zh-Hans"}, {"chat_size": 99}, {"chat_size": "two"}, {"chat_size": True},
            {"min_silence_ms": -1}, {"lead_ms": 0}, {"lead_ms": 99999}, {"device": -1}, {"device": ""}, {"device": 1}, {"model": ""}):
    assert p1.update(bad) is False and p1.cfg == bridge.DEFAULTS, f"accepted {bad}"
# the mic is stored by name, because indices shuffle whenever a Bluetooth device comes or goes
assert p1.update({"device": "AirPods Pro"}) is False, "picking a mic should not need a restart"
assert p1.cfg["device"] == "AirPods Pro"

# a mic that is named in the config but not plugged in falls back to the system default,
# because the built-in microphone beats no translation at all
class FakeSD:
    opened = None
    @staticmethod
    def query_devices(): return [{"name": "MacBook Pro Microphone", "max_input_channels": 1}]
    @staticmethod
    def RawInputStream(**kw):
        FakeSD.opened = kw["device"]
        return type("S", (), {"start": lambda self: None, "close": lambda self: None})()

sys.modules["sounddevice"] = FakeSD
p1.open_mic()
assert FakeSD.opened is None, f"opened {FakeSD.opened!r} instead of falling back"
p1.update({"device": "MacBook Pro Microphone"})
p1.open_mic()
assert FakeSD.opened == "MacBook Pro Microphone", f"opened {FakeSD.opened!r}"
del sys.modules["sounddevice"]
p1.mic = None
p1.cfg = dict(bridge.DEFAULTS)

assert p1.update({"source": "auto"}) is True, "spoken language should need a restart"
assert p1.update({"target": "en"}) is False, "target only changes the prompt"

# a hand-edited config.json with a bad value falls back instead of crashing at startup
bridge.CONFIG_PATH.write_text(json.dumps({"source": "klingon", "target": "en", "chat_size": 3}))
loaded = bridge.load_config()
assert loaded["source"] == bridge.DEFAULTS["source"], "bad value survived load"
assert loaded["target"] == "en" and loaded["chat_size"] == 3, "good values were dropped"

# a config written before 简体/繁體 was dropped still starts, quietly, on the same language
for legacy in ("zh-Hans", "zh-Hant"):
    bridge.CONFIG_PATH.write_text(json.dumps({"target": legacy}))
    assert bridge.load_config()["target"] == "zh", f"{legacy} did not migrate"

# config: only known keys, persisted, and only model-ish changes demand a restart
p = bridge.Pipeline()
assert p.update({"device": "AirPods Pro"}) is False, "changing the mic should not need a restart"
assert p.update({"chat_size": 4}) is True, "changing context should need a restart"
assert p.update({"chat_size": 4}) is False, "re-saving the same value is not a change"
assert p.update({"nonsense": 1, "model": "m"}) is True
saved = json.loads(bridge.CONFIG_PATH.read_text())
assert saved["device"] == "AirPods Pro" and saved["chat_size"] == 4 and saved["model"] == "m"
assert "nonsense" not in saved, "unknown key reached the config file"
assert set(saved) == set(bridge.DEFAULTS), "config file shape drifted from DEFAULTS"

# the QR has to scale to whatever box the console gives it. A fixed width and no viewBox
# gets clipped by max-width rather than resized, which is what knocked it off centre.
try:                                   # segno is optional, exactly as it is in bridge.py
    import segno
    qr = segno.make("http://192.168.1.50:8000/").svg_inline(scale=6, omitsize=True)
    assert "viewBox=" in qr, "no viewBox: the QR cannot scale"
    assert "width=" not in qr.split(">")[0], "a fixed width will clip inside max-width"
except ImportError:
    print("(skipping the QR check: segno is not installed)")

# an isolated church LAN with no route to the internet must not break the status page
real_probe, real_resolve = bridge._probe_route, socket.gethostbyname
def unreachable(*a, **k): raise OSError("Network is unreachable")
bridge._probe_route = unreachable
socket.gethostbyname = unreachable
bridge._ip_cache = (0.0, "127.0.0.1")
assert bridge.lan_ip() == "127.0.0.1", "no route should fall back, not raise"
bridge._probe_route = lambda: "192.168.1.50"
bridge._ip_cache = (0.0, "127.0.0.1")
assert bridge.lan_ip() == "192.168.1.50"
assert bridge.lan_ip() == "192.168.1.50", "second call should come from the cache"
bridge._probe_route, socket.gethostbyname = real_probe, real_resolve

# a dying encoder has to surface: both encoder threads are daemons nobody watches
p2 = bridge.Pipeline()
saved, bridge.pipeline = bridge.pipeline, p2
p2.state = "running"
class Dead:
    def write(self, b): raise BrokenPipeError("ffmpeg is gone")
    def flush(self): pass
stop = threading.Event()
th = threading.Thread(target=bridge.pacer, args=(Dead(), stop), daemon=True)
th.start(); th.join(2)
assert not th.is_alive(), "pacer should return when the encoder dies"
assert p2.state == "error" and "encoder" in p2.detail, f"death not surfaced: {p2.state}"
bridge.pipeline = saved

# the console must not be reachable from the church wifi
class Stub(bridge.Handler):
    def __init__(self, addr): self.client_address = (addr, 1); self.err = None
    def send_error(self, code, msg=None): self.err = code

assert Stub("127.0.0.1").local_only() is True
assert Stub("::1").local_only() is True
blocked = Stub("192.168.1.40")
assert blocked.local_only() is False and blocked.err == 403, "console exposed to the LAN"

# A termination signal must run the cleanup, not kill the interpreter where it stands.
# launchd sends TERM and closing the Terminal window sends HUP; without a handler for
# both, ffmpeg and the pipeline are orphaned.
import os, subprocess, sys
here = Path(__file__).parent
# The child writes config.json in its own directory. Hold whatever the operator had saved
# there and put it back afterwards, so running the self-check never costs them their setup.
saved_config = (here / "config.json").read_bytes() if (here / "config.json").exists() else None
proc = subprocess.Popen([sys.executable, str(here / "bridge.py"), "--no-start"],
                        cwd=here, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        start_new_session=True)
pgid = os.getpgid(proc.pid)          # read it now: it is gone once the process exits
encoder = lambda: subprocess.run(["pgrep", "-g", str(pgid), "-f", "libmp3lame"],
                                 capture_output=True).returncode == 0
try:
    for _ in range(40):
        if encoder():
            break
        time.sleep(.25)
    else:
        raise AssertionError("the encoder never started")
    proc.send_signal(signal.SIGHUP)      # what closing the Terminal window sends
    code = proc.wait(timeout=15)
    # 0 means the handler ran and the finally block cleaned up; -15 means Python was killed
    # where it stood, which is what orphans the pipeline subprocess.
    assert code == 0, f"the signal killed it outright (exit {code}); cleanup never ran"
    time.sleep(1)
    assert not encoder(), "the encoder outlived the bridge"
finally:
    if proc.poll() is None:
        proc.kill()
    if saved_config is None:
        (here / "config.json").unlink(missing_ok=True)
    else:
        (here / "config.json").write_bytes(saved_config)

# stop() has to take the pipeline subprocess with it; that is the one that does not die
# on its own when the bridge goes away.
p4 = bridge.Pipeline()
p4.proc = subprocess.Popen(["sleep", "300"])
pid = p4.proc.pid
p4.stop()
time.sleep(.5)
assert subprocess.run(["ps", "-p", str(pid)], capture_output=True).returncode != 0, \
    "stop() left the pipeline process running"

print("ok")
