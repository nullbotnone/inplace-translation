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

// what an <audio> reports as downloaded: a browser can hold several ranges at once
const buffered = (from, to) => ({ length: 1, start: () => from, end: () => to });
const ids = Object.fromEntries(["a", "b", "log", "buttonText", "playHint", "connection",
  "connectionText"].map((id) => [id, el(id === "a" ? "audio" : "div")]));
const empty = el("li"); empty.className = "empty"; ids.log.append(empty);
let eventSource;
const ticks = [];
const sandbox = {
  document: { getElementById: (id) => ids[id], createElement: (tag) => el(tag) },
  EventSource: class { constructor() { eventSource = this; } },
  setInterval: (fn) => ticks.push(fn) - 1,
  clearInterval: (id) => { ticks[id] = null; },
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

const tick = () => ticks.forEach((fn) => fn && fn());   // every interval the page set
const latest = () => ids.log.children.at(-1).children[1].textContent;   // the transcript is capped
const say = (kind, text, secs) =>
  eventSource.onmessage({ data: JSON.stringify({ kind, text, at: "10:41:00", secs }) });

// A translation is held until the playhead reaches the position where its own audio sits --
// the end of what this phone holds when the line arrives. Two seconds of buffer is two
// seconds of waiting, and the wait is a position rather than a timer, so nothing about a
// rebuffer or the 6% catch-up can move it.
ids.a.paused = false;
ids.a.currentTime = 100;
ids.a.buffered = buffered(0, 102);
say("out", "held for the voice", 0);
tick();
if (latest() === "held for the voice") throw new Error("shown two seconds before its audio");
ids.a.currentTime = 101.9; tick();
if (latest() === "held for the voice") throw new Error("shown before the playhead reached it");
ids.a.currentTime = 102; tick();
if (latest() !== "held for the voice") throw new Error("never shown once the voice reached it");

// ... what was heard does not wait: nothing is reading it out
ids.a.currentTime = 100; ids.a.buffered = buffered(0, 105);
say("src", "heard right away");
if (latest() !== "heard right away") throw new Error("what the preacher said was held back");

// ... nor does anything while the phone is only reading
ids.a.paused = true;
say("out", "read live", 0);
if (latest() !== "read live") throw new Error("a line was held on a phone that is only reading");

// A line that arrives while the browser is still filling its buffer is the one most likely
// to be wrong: the playhead has not moved, so a delay measured against it is zero and the
// line goes up seconds before any audio plays. Its audio is still at the end of the buffer.
ids.b.onclick();                                   // asks for audio; playback has not begun
ids.a.paused = true; ids.a.currentTime = 0; ids.a.buffered = buffered(0, 3);
say("out", "first line of the sermon", 0);
tick();
if (latest() === "first line of the sermon") throw new Error("shown while the phone was still buffering");
ids.a.paused = false; ids.a.currentTime = 3; tick();
if (latest() !== "first line of the sermon") throw new Error("never shown once playback caught up");

// Whisper re-transcribes a turn as the speaker keeps going, correcting earlier words on the
// way. Each pass carries the same id, and revises the line already on screen.
const nthLast = (n) => ids.log.children.at(-n).children[1].textContent;  // the log is capped
eventSource.onmessage({ data: JSON.stringify({ kind: "src", text: "死亡的原因", at: "10:41:00", id: 77 }) });
eventSource.onmessage({ data: JSON.stringify({ kind: "src", text: "死亡的原因是什么呢", at: "10:41:00", id: 77 }) });
if (nthLast(1) !== "死亡的原因是什么呢") throw new Error(`the line was not revised: ${nthLast(1)}`);
if (nthLast(2) === "死亡的原因") throw new Error("the unfinished pass was left on screen");
eventSource.onmessage({ data: JSON.stringify({ kind: "src", text: "我们翻到第二章", at: "10:41:05", id: 78 }) });
if (nthLast(1) !== "我们翻到第二章" || nthLast(2) !== "死亡的原因是什么呢")
  throw new Error("a new turn did not start its own line");

// A line is revealed as the voice says it, on the voice's own clock: this page plays 6%
// fast while it catches up to the live edge and stops dead while a phone rebuffers, and the
// words have to do both with it. Chinese counts by the character, English by the word.
ids.a.currentTime = 100; ids.a.buffered = buffered(0, 100);
say("out", "神爱世人", 4);
tick();
const body = ids.log.children.at(-1).children[1];
if (body.textContent !== "神") throw new Error(`expected the first character only, got ${body.textContent}`);
ids.a.currentTime = 102; tick();          // half spoken
if (body.textContent !== "神爱世") throw new Error(`at half the sentence: ${body.textContent}`);
ids.a.currentTime = 101; tick();          // the voice cannot go backwards; nor does the text
if (body.textContent !== "神爱世") throw new Error("the reveal went backwards");
ids.a.currentTime = 104; tick();
if (body.textContent !== "神爱世人") throw new Error("the line never completed");
ids.a.currentTime = 999; tick();          // finished lines stop costing anything

// English is counted by the word
ids.a.buffered = buffered(0, 999);
say("out", "Grace and peace", 3);
tick();
const english = ids.log.children.at(-1).children[1];
if (english.textContent !== "Grace") throw new Error(`expected one word, got ${english.textContent}`);
ids.a.currentTime = 1002; tick();
if (english.textContent !== "Grace and peace") throw new Error("the English line never completed");

// Whatever the phone buffered on the way in never drains by itself: the stream carries
// silence between sentences, so the playhead keeps its distance from the live edge and the
// voice lags the subtitle of the same sentence forever. Play slightly fast until it closes.
const behind = (seconds) => {
  ids.a.currentTime = 100;
  ids.a.buffered = buffered(0, 100 + seconds);
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

// A reconnect leaves the old buffered range behind and starts a new one. The gap to a range
// this phone is not playing is not a lag: measured against the last range it would chase for
// the rest of the service and hold every subtitle for the 5 s maximum.
ids.a.paused = false;
ids.a.playbackRate = 1;
ids.a.buffered = { length: 2, start: (i) => [0, 900][i], end: (i) => [200, 1003][i] };
ids.a.currentTime = 199.8;                 // playing the first range, a fifth of a second behind
tick();
if (ids.a.playbackRate !== 1)
  throw new Error(`chased ${ids.a.playbackRate}x against a range it is not playing`);

eventSource.onerror();
if (ids.connection.dataset.state !== "offline") throw new Error("disconnect was not surfaced");
console.log("ok: listener UI connects, preserves history at the live edge, reveals lines as "
  + "they are spoken, chases the live audio edge, escapes text, and toggles audio");
