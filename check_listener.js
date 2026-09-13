/* Exercises index.html's live UI against a tiny DOM. This catches playback-state and
 * subtitle regressions without needing a real sermon or audio stream.
 * Run: node check_listener.js
 */
const fs = require("fs");
const vm = require("node:vm");
const src = fs.readFileSync(`${__dirname}/index.html`, "utf8");
const script = src.slice(src.indexOf("<script>") + 8, src.lastIndexOf("</script>"));

function el(tag = "div") {
  const listeners = {};
  const node = {
    tag, dataset: {}, children: [], className: "", textContent: "", paused: true,
    src: "", scrollTop: 0, scrollHeight: 100,
    setAttribute(k, v) { this[k] = v; },
    addEventListener(name, fn) { listeners[name] = fn; },
    emit(name) { listeners[name]?.(); },
    append(...kids) { kids.forEach((kid) => (kid.parent = this)); this.children.push(...kids); },
    appendChild(kid) { kid.parent = this; this.children.push(kid); return kid; },
    remove() {
      if (!this.parent) return;
      this.parent.children.splice(this.parent.children.indexOf(this), 1);
    },
    querySelector(sel) {
      if (sel === ".empty") return this.children.find((kid) => kid.className === "empty") ?? null;
      const kind = sel.match(/^\[data-kind="(src|out)"\]$/)?.[1];
      return kind ? this.children.find((kid) => kid.dataset.kind === kind) ?? null : null;
    },
    play() { this.paused = false; return Promise.resolve(); },
    pause() { this.paused = true; },
    currentTime: 0, playbackRate: 1,
  };
  Object.defineProperty(node, "firstChild", { get: () => node.children[0] ?? null });
  return node;
}

const ids = Object.fromEntries(["a", "b", "log", "buttonText", "playHint", "connection",
  "connectionText"].map((id) => [id, el(id === "a" ? "audio" : "div")]));
const empty = el("li"); empty.className = "empty"; ids.log.append(empty);
let eventSource;
const ticks = [];
const sandbox = {
  document: { getElementById: (id) => ids[id], createElement: (tag) => el(tag) },
  EventSource: class { constructor() { eventSource = this; } },
  setInterval: (fn) => ticks.push(fn),
  setTimeout, clearTimeout, console,
};
vm.createContext(sandbox);
vm.runInContext(script, sandbox, { filename: "index.html" });

eventSource.onopen();
if (ids.connection.dataset.state !== "live") throw new Error("connection state never became live");

const line = JSON.stringify({ kind: "out", text: "<b>神爱世人</b>", at: "10:31:02" });
eventSource.onmessage({ data: line });
eventSource.onmessage({ data: line }); // the server replays recent lines after reconnects
if (ids.log.children.length !== 1) throw new Error("a replayed subtitle was duplicated");
if (ids.log.children[0].children[1].textContent !== "<b>神爱世人</b>")
  throw new Error("subtitle text was not rendered safely");

for (let i = 0; i < 60; i++)
  eventSource.onmessage({ data: JSON.stringify({ kind: i % 2 ? "src" : "out", text: `line ${i}`, at: `10:32:${i}` }) });
if (ids.log.children.length !== 40) throw new Error(`expected bounded transcript history, got ${ids.log.children.length}`);
if (ids.log.children.at(-1).children[1].textContent !== "line 59")
  throw new Error("the latest transcript line was not retained");
if (ids.log.scrollTop !== ids.log.scrollHeight)
  throw new Error("the transcript did not return to the live edge");

ids.b.onclick();
if (ids.b.dataset.mode !== "loading") throw new Error("play button did not show its loading state");
ids.a.emit("playing");
if (ids.b.dataset.mode !== "playing") throw new Error("play button did not show its playing state");
ids.b.onclick();
if (ids.b.dataset.mode !== "idle" || !ids.a.paused) throw new Error("audio could not be paused");

// A phone playing two seconds behind the live edge must hold the subtitle by the same two
// seconds, or the text arrives before the voice reading it. With audio off, nothing to wait
// for. Timers are captured rather than run, so the test does not have to sleep.
const pending = [];
sandbox.setTimeout = (fn, ms) => { pending.push([fn, ms]); return 0; };
ids.a.paused = false;
ids.a.currentTime = 100;
ids.a.buffered = { length: 1, end: () => 102 };
eventSource.onmessage({ data: JSON.stringify({ kind: "out", text: "held for the voice", at: "10:40:00" }) });
if (!pending.length) throw new Error("a subtitle was shown while the voice was two seconds behind");
if (pending[0][1] < 1500) throw new Error(`held only ${pending[0][1]}ms for a 2 s lag`);
const latest = () => ids.log.children.at(-1).children[1].textContent;   // the transcript is capped
pending[0][0]();
if (latest() !== "held for the voice") throw new Error("the held subtitle never appeared");
pending.length = 0;
ids.a.paused = true;
eventSource.onmessage({ data: JSON.stringify({ kind: "out", text: "read live", at: "10:40:01" }) });
if (latest() !== "read live" || pending.length)
  throw new Error("a subtitle was held back on a phone that is only reading");
ids.a.paused = false;

// Whatever the phone buffered on the way in never drains by itself: the stream carries
// silence between sentences, so the playhead keeps its distance from the live edge and the
// voice lags the subtitle of the same sentence forever. Play slightly fast until it closes.
const tick = () => ticks.forEach((fn) => fn());
const behind = (seconds) => {
  ids.a.currentTime = 100;
  ids.a.buffered = { length: 1, end: () => 100 + seconds };
};
ids.a.paused = false;
behind(1.2); tick();
if (ids.a.playbackRate <= 1) throw new Error("a phone a second behind never caught up");
behind(3); tick();
if (ids.a.playbackRate < 1.1) throw new Error("three seconds behind was chased no harder");
behind(0.3); tick();
if (ids.a.playbackRate !== 1) throw new Error("playback stayed fast after catching up");
ids.a.paused = true; ids.a.playbackRate = 1;
behind(5); tick();
if (ids.a.playbackRate !== 1) throw new Error("paused audio was chased");

eventSource.onerror();
if (ids.connection.dataset.state !== "offline") throw new Error("disconnect was not surfaced");
console.log("ok: listener UI connects, preserves history at the live edge, chases the live "
  + "audio edge, escapes text, and toggles audio");
