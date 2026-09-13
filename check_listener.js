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
    // A translation goes under the line it translates, so the page needs a real insert: with
    // an appendChild that ignores its reference node this check could never see the order go
    // wrong. A missing reference appends, the way the DOM does.
    insertBefore(kid, before) {
      kid.parent = this;
      const at = before ? this.children.indexOf(before) : -1;
      if (at >= 0) this.children.splice(at, 0, kid); else this.children.push(kid);
      return kid;
    },
    // A node that is not in the tree removes to nothing, the way the DOM does; splicing at
    // an index of -1 would take the last child with it instead.
    remove() {
      const at = this.parent ? this.parent.children.indexOf(this) : -1;
      if (at >= 0) this.parent.children.splice(at, 1);
      this.parent = null;
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
  Object.defineProperty(node, "parentNode", { get: () => node.parent ?? null });
  Object.defineProperty(node, "nextSibling", { get: () => {
    const kids = node.parent ? node.parent.children : [];
    return kids[kids.indexOf(node) + 1] ?? null;
  } });
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

// the waiting dots are furniture, not a line of sermon
const lines = () => ids.log.children.filter((c) => c.className !== "awaiting");
const dots = () => ids.log.children.find((c) => c.className === "awaiting");

eventSource.onopen();
if (ids.connection.dataset.state !== "live") throw new Error("connection state never became live");

const line = JSON.stringify({ kind: "out", text: "<b>神爱世人</b>", at: "10:31:02" });
eventSource.onmessage({ data: line });
eventSource.onmessage({ data: line }); // the server replays recent lines after reconnects
if (lines().length !== 1) throw new Error("a replayed subtitle was duplicated");
if (lines()[0].children[1].textContent !== "<b>神爱世人</b>")
  throw new Error("subtitle text was not rendered safely");

for (let i = 0; i < 60; i++)
  eventSource.onmessage({ data: JSON.stringify({ kind: i % 2 ? "src" : "out", text: `line ${i}`, at: `10:32:${i}` }) });
if (lines().length !== 40) throw new Error(`expected bounded transcript history, got ${lines().length}`);
if (lines().at(-1).children[1].textContent !== "line 59")
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
const latest = () => lines().at(-1).children[1].textContent;   // the transcript is capped
const say = (kind, text, secs) =>
  eventSource.onmessage({ data: JSON.stringify({ kind, text, at: "10:41:00", secs }) });

// If the subtitle socket wins the race at startup, there is no buffered edge yet. The text
// must wait for one instead of popping up before the phone can play any of its voice.
ids.a.paused = true; ids.a.currentTime = 0; ids.a.buffered = { length: 0 };
ids.b.onclick();
say("out", "wait for the first audio", 2);
tick();
if (latest() === "wait for the first audio") throw new Error("subtitle beat the initial audio buffer");
ids.a.buffered = buffered(0, 1); tick();
if (latest() === "wait for the first audio") throw new Error("subtitle ignored the new audio buffer");
ids.a.paused = false; ids.a.currentTime = 1; tick();
if (!latest().startsWith("wait")) throw new Error("subtitle never joined its first audio");
ids.b.onclick();

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
const nthLast = (n) => lines().at(-n).children[1].textContent;  // the log is capped
eventSource.onmessage({ data: JSON.stringify({ kind: "src", text: "死亡的原因", at: "10:41:00", id: 77 }) });
eventSource.onmessage({ data: JSON.stringify({ kind: "src", text: "死亡的原因是什么呢", at: "10:41:00", id: 77 }) });
if (nthLast(1) !== "死亡的原因是什么呢") throw new Error(`the line was not revised: ${nthLast(1)}`);
if (nthLast(2) === "死亡的原因") throw new Error("the unfinished pass was left on screen");
eventSource.onmessage({ data: JSON.stringify({ kind: "src", text: "我们翻到第二章", at: "10:41:05", id: 78 }) });
if (nthLast(1) !== "我们翻到第二章" || nthLast(2) !== "死亡的原因是什么呢")
  throw new Error("a new turn did not start its own line");

// A translation is held until the voice reaches it, so the screen would otherwise sit there
// looking broken. Three dots under the heard line, until its translation arrives.
eventSource.onmessage({ data: JSON.stringify({ kind: "src", text: "等待翻译", at: "10:41:10", id: 80 }) });
if (!dots()) throw new Error("nothing showed that a translation was still coming");
if (ids.log.children.at(-1).className !== "awaiting") throw new Error("the dots are not under the newest line");
eventSource.onmessage({ data: JSON.stringify({ kind: "src", text: "等待翻译的下一段", at: "10:41:10", id: 80 }) });
if (ids.log.children.filter((c) => c.className === "awaiting").length !== 1)
  throw new Error("a re-transcription added a second set of dots");
eventSource.onmessage({ data: JSON.stringify({ kind: "out", text: "Waiting for this", at: "10:41:12", id: 81 }) });
if (!dots()) throw new Error("the dots left before the translation was on screen");
tick();                                    // ... the voice reaches it and the line appears
if (dots()) throw new Error("the dots outlived the translation they were waiting for");
if (latest() !== "Waiting for this") throw new Error("the translation never appeared");
// ... and a turn the translator answers with nothing does not leave them spinning: the next
// translation clears them, whatever went untranslated before it
eventSource.onmessage({ data: JSON.stringify({ kind: "src", text: "未翻译的一句", at: "10:41:13", id: 82 }) });
eventSource.onmessage({ data: JSON.stringify({ kind: "src", text: "下一句", at: "10:41:14", id: 83 }) });
eventSource.onmessage({ data: JSON.stringify({ kind: "out", text: "The next one", at: "10:41:15", id: 84 }) });
tick();
if (dots()) throw new Error("an untranslated turn left the dots spinning");

// A translation waits for the voice, so by the time it arrives the preacher's next sentence
// has already been heard and gone up. It still belongs under the sentence it translates --
// the bridge names which one -- rather than at the bottom under somebody else's words.
const heard = (text, at, id) =>
  eventSource.onmessage({ data: JSON.stringify({ kind: "src", text, at, id }) });
const translated = (text, at, id, reply_to) =>
  eventSource.onmessage({ data: JSON.stringify({ kind: "out", text, at, id, reply_to }) });
heard("死亡这个话题重大", "14:30:55", 90);
heard("他就不能有智慧的活着", "14:31:00", 91);
translated("The topic of death is significant", "14:31:01", 92, 90);
tick();
if (nthLast(2) !== "The topic of death is significant" || nthLast(1) !== "他就不能有智慧的活着")
  throw new Error(`the translation was filed under the wrong line: ${[nthLast(3), nthLast(2), nthLast(1)]}`);
// ... and the sentence still waiting for its own voice keeps the dots
if (!dots()) throw new Error("the dots left a heard line whose translation had not arrived");
translated("they cannot live wisely.", "14:31:07", 93, 91);
tick();
if (nthLast(1) !== "they cannot live wisely.")
  throw new Error("the newest translation did not land under the newest heard line");
if (dots()) throw new Error("the dots outlived the translation they were waiting for");

// A line is revealed as the voice says it, on the voice's own clock: this page plays 6%
// fast while it catches up to the live edge and stops dead while a phone rebuffers, and the
// words have to do both with it. Chinese counts by the character, English by the word.
ids.a.currentTime = 100; ids.a.buffered = buffered(0, 100);
say("out", "神爱世人", 4);
tick();
const body = lines().at(-1).children[1];
if (body.textContent !== "神") throw new Error(`expected the first character only, got ${body.textContent}`);
ids.a.currentTime = 102; tick();          // half spoken
if (body.textContent !== "神爱世") throw new Error(`at half the sentence: ${body.textContent}`);
ids.a.currentTime = 101; tick();          // the voice cannot go backwards; nor does the text
if (body.textContent !== "神爱世") throw new Error("the reveal went backwards");
ids.a.currentTime = 104; tick();
if (body.textContent !== "神爱世人") throw new Error("the line never completed");
ids.a.currentTime = 999; tick();          // finished lines stop costing anything

// A delayed release starts at the voice's booked position, not at the late UI tick. After a
// one-second stall the reveal must catch up to the voice instead of beginning again at word 1.
ids.a.currentTime = 200; ids.a.buffered = buffered(0, 200);
say("out", "我们彼此相爱", 6);
ids.a.currentTime = 203; tick();
const caughtUp = lines().at(-1).children[1];
if (caughtUp.textContent.length < 4)
  throw new Error(`late subtitle restarted behind the voice: ${caughtUp.textContent}`);

// English is counted by the word
ids.a.currentTime = 999;
ids.a.buffered = buffered(0, 999);
say("out", "Grace and peace", 3);
tick();
const english = lines().at(-1).children[1];
if (english.textContent !== "Grace") throw new Error(`expected one word, got ${english.textContent}`);
ids.a.currentTime = 1002; tick();
if (english.textContent !== "Grace and peace") throw new Error("the English line never completed");

// A pause after punctuation is part of speech timing. A long word after a comma should not
// pop up on the old character-count schedule while the voice is still taking that pause.
ids.a.currentTime = 1100; ids.a.buffered = buffered(0, 1100);
say("out", "I, therefore, urge", 6);
tick();
const punctuated = lines().at(-1).children[1];
ids.a.currentTime = 1100.8; tick();
if (punctuated.textContent !== "I,")
  throw new Error(`English punctuation pause was skipped: ${punctuated.textContent}`);
ids.a.currentTime = 1106; tick();
if (punctuated.textContent !== "I, therefore, urge")
  throw new Error("punctuated English line never completed");

// Keep a safe jitter buffer. The old catch-up drained this to 0.4 s, so a short Wi-Fi gap
// stopped the voice in the middle of a sentence. A large reserve is still chased gently,
// while a critical one slows down enough to give the next network burst time to arrive.
const behind = (seconds) => {
  ids.a.currentTime = 100;
  ids.a.buffered = buffered(0, 100 + seconds);
};
ids.a.paused = false;
behind(1.2); tick();
if (ids.a.playbackRate !== 1) throw new Error("the safe audio reserve was drained");
behind(3); tick();
if (ids.a.playbackRate <= 1) throw new Error("an excessive audio reserve was never chased");
behind(0.3); tick();
if (ids.a.playbackRate >= 1) throw new Error("a critical audio reserve was not protected");
behind(1.5); tick();
if (ids.a.playbackRate !== 1) throw new Error("playback did not settle at its safe reserve");

// A real underrun teaches this phone to keep a larger reserve. After waiting, two seconds is
// no longer treated as spare audio to burn through at a faster playback rate.
ids.a.emit("playing");
behind(2.2); tick();
if (ids.a.playbackRate <= 1) throw new Error("baseline catch-up did not engage");
ids.a.emit("waiting"); tick();
if (ids.a.playbackRate !== 1) throw new Error("playback kept draining after an underrun");
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
if (ids.a.playbackRate > 1)
  throw new Error(`chased ${ids.a.playbackRate}x toward a range it is not playing`);
ids.a.currentTime = 500;                   // stalled in the gap; neither range is live
ids.a.playbackRate = 1;
tick();
if (ids.a.playbackRate !== 1)
  throw new Error(`chased ${ids.a.playbackRate}x across an unbuffered gap`);

eventSource.onerror();
if (ids.connection.dataset.state !== "offline") throw new Error("disconnect was not surfaced");
console.log("ok: listener UI connects, preserves history at the live edge, reveals lines as "
  + "they are spoken, protects its audio buffer, escapes text, and toggles audio");
