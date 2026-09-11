"""Self-check: python3 test_bridge.py"""
import json, queue, socket, tempfile, threading, time
from pathlib import Path

import bridge

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

# junk never reaches a CLI flag or a dict lookup
p1 = bridge.Pipeline()
for bad in ({"source": "klingon"}, {"target": "zh-Hanzi"}, {"tts": "; rm -rf /"},
            {"target": "zh-Hans"}, {"chat_size": 99}, {"chat_size": "two"}, {"chat_size": True},
            {"min_silence_ms": -1}, {"device": "webcam"}, {"model": ""}):
    assert p1.update(bad) is False and p1.cfg == bridge.DEFAULTS, f"accepted {bad}"
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
assert p.update({"device": 3}) is False, "changing the mic should not need a restart"
assert p.update({"chat_size": 4}) is True, "changing context should need a restart"
assert p.update({"chat_size": 4}) is False, "re-saving the same value is not a change"
assert p.update({"nonsense": 1, "model": "m"}) is True
saved = json.loads(bridge.CONFIG_PATH.read_text())
assert saved["device"] == 3 and saved["chat_size"] == 4 and saved["model"] == "m"
assert "nonsense" not in saved, "unknown key reached the config file"
assert set(saved) == set(bridge.DEFAULTS), "config file shape drifted from DEFAULTS"

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

print("ok")
