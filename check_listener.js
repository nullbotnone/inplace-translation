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
    querySelector(sel) { return sel === ".empty" ? this.children.find((kid) => kid.className === "empty") : null; },
    play() { this.paused = false; return Promise.resolve(); },
    pause() { this.paused = true; },
  };
  Object.defineProperty(node, "firstChild", { get: () => node.children[0] ?? null });
  return node;
}

const ids = Object.fromEntries(["a", "b", "log", "buttonText", "playHint", "connection",
  "connectionText"].map((id) => [id, el(id === "a" ? "audio" : "div")]));
const empty = el("li"); empty.className = "empty"; ids.log.append(empty);
let eventSource;
const sandbox = {
  document: { getElementById: (id) => ids[id], createElement: (tag) => el(tag) },
  EventSource: class { constructor() { eventSource = this; } },
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
if (ids.log.children.length > 40) throw new Error(`subtitle history grew to ${ids.log.children.length}`);

ids.b.onclick();
if (ids.b.dataset.mode !== "loading") throw new Error("play button did not show its loading state");
ids.a.emit("playing");
if (ids.b.dataset.mode !== "playing") throw new Error("play button did not show its playing state");
ids.b.onclick();
if (ids.b.dataset.mode !== "idle" || !ids.a.paused) throw new Error("audio could not be paused");

eventSource.onerror();
if (ids.connection.dataset.state !== "offline") throw new Error("disconnect was not surfaced");
console.log("ok: listener UI connects, deduplicates and bounds subtitles, escapes text, and toggles audio");
