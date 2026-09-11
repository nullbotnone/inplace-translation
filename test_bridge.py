"""Self-check: python3 test_bridge.py"""
import json, queue, tempfile, threading, time
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

# the glossary is read fresh each time, so edits apply without a restart
assert bridge.BASE_PROMPT in bridge.instructions()
assert "Grace Chapel" not in bridge.instructions()
bridge.GLOSSARY_PATH.write_text("  Grace Chapel -> 恩典堂  ")
assert "Grace Chapel -> 恩典堂" in bridge.instructions(), "glossary edit not picked up"

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

# the console must not be reachable from the church wifi
class Stub(bridge.Handler):
    def __init__(self, addr): self.client_address = (addr, 1); self.err = None
    def send_error(self, code, msg=None): self.err = code

assert Stub("127.0.0.1").local_only() is True
assert Stub("::1").local_only() is True
blocked = Stub("192.168.1.40")
assert blocked.local_only() is False and blocked.err == 403, "console exposed to the LAN"

print("ok")
