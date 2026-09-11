"""Self-check: python3 test_bridge.py"""
import queue, threading, time, bridge


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
w = b"".join(run_pacer(0.1))
assert w.startswith(bytes(range(256)) * 5), "queued audio not emitted first"

# backlog past MAX_LAG_S is dropped rather than drifting forever
for _ in range(int(bridge.MAX_LAG_S * bridge.RATE * 2 / 640) + 10):
    bridge.out_q.put(b"\1" * 640)
run_pacer(0.1)
assert bridge.out_q.qsize() < 10, f"backlog not dropped: {bridge.out_q.qsize()}"

# a listener that stops draining gets evicted instead of growing without bound
f = bridge.Fanout(maxq=3)
with f.subscribe() as fast, f.subscribe() as slow:
    for i in range(6):
        f.publish(b"x")
        fast.get()
    assert slow not in f.qs, "lagging listener was not dropped"
    assert fast in f.qs, "keeping-up listener was dropped"
assert not f.qs, "subscribe() did not clean up on exit"

# subtitles reach live listeners, and a phone joining late gets the recent backlog
with bridge.subs.subscribe() as q:
    bridge.subtitle("src", " For God so loved the world ")
    bridge.subtitle("out", "神爱世人")
    bridge.subtitle("out", "   ")                     # blank turns are not broadcast
    assert q.get_nowait() == b'data: {"kind": "src", "text": "For God so loved the world"}\n\n'
    assert b"\\u795e\\u7231\\u4e16\\u4eba" in q.get_nowait(), "translation not sent"
    assert q.empty(), "empty subtitle was broadcast"
assert len(bridge.recent) == 2 and "world" in bridge.recent[0], "backlog not kept"
for _ in range(20):
    bridge.subtitle("out", "x")
assert len(bridge.recent) == 8, "backlog not capped"

print("ok")
