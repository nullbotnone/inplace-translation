/* Runs admin.html's script against a stub DOM and drives it with a realistic status
 * event. node --check only parses; this catches what parsing cannot — a variable used
 * before its const, a typo'd property, a handler that throws halfway and silently
 * leaves the rest of the page blank. Run: node check_console.js
 */
const fs = require("fs");
const src = fs.readFileSync(`${__dirname}/admin.html`, "utf8");
const script = src.slice(src.indexOf("<script>") + 8, src.lastIndexOf("</script>"));

const ids = [...src.matchAll(/\bid="([^"]+)"/g)].map((m) => m[1]);
// keep track of which translatable nodes are <option>s: rewriting those under an open
// native dropdown closes it, so a status repaint must never touch them
const attrEls = [...src.matchAll(/<([a-z]+)[^>]*\bdata-en="[^"]*"[^>]*>/g)]
  .map((m) => el(m[1]));

function el(tag = "div") {
  const e = {
    tag, dataset: {}, style: {}, children: [], value: "", innerHTML: "",
    className: "", placeholder: "", disabled: false, type: "",
    classList: { on: new Set(), add(c) { this.on.add(c); }, remove(c) { this.on.delete(c); },
                 toggle(c, want) { want ? this.on.add(c) : this.on.delete(c); },
                 contains(c) { return this.on.has(c); } },
    setAttribute(k, v) { this[k] = v; }, getAttribute(k) { return this[k]; },
    appendChild(c) { c.parent = this; this.children.push(c); return c; },
    // A real insert, not an append that ignores its reference node: a translation belongs
    // under the line it translates, and an append could never show that going wrong.
    insertBefore(c, before) {
      c.parent = this;
      const at = before ? this.children.indexOf(before) : -1;
      if (at >= 0) this.children.splice(at, 0, c); else this.children.push(c);
      return c;
    },
    append(...c) { c.forEach((x) => { x.parent = this; }); this.children.push(...c); },
    querySelector(sel) {
      return sel === ".empty" ? this.children.find((kid) => kid.className === "empty") ?? null : null;
    },
    querySelectorAll: () => [], closest: () => null,
    // A node that is not in the tree removes to nothing, the way the DOM does; splicing at
    // an index of -1 would take the last child with it instead.
    remove() {
      const kids = this.parent && this.parent.children;
      const at = kids ? kids.indexOf(this) : -1;
      if (at >= 0) kids.splice(at, 1);
      this.parent = null;
    },
    focus() {}, setSelectionRange() {}, addEventListener() {},
    replaceChildren(...kids) { e.children = kids; kids.forEach((k) => (k.parent = e)); },
    scrollHeight: 0, scrollTop: 0, clientHeight: 0,
  };
  let text = "";
  e.writes = 0;
  Object.defineProperty(e, "textContent", {
    get: () => text,
    set(v) { text = v; e.writes++; },
  });
  Object.defineProperty(e, "firstChild", { get: () => e.children[0] ?? null });
  Object.defineProperty(e, "parentNode", { get: () => e.parent ?? null });
  Object.defineProperty(e, "nextSibling", { get: () => {
    const kids = e.parent ? e.parent.children : [];
    return kids[kids.indexOf(e) + 1] ?? null;
  } });
  return e;
}

const byId = Object.fromEntries(ids.map((i) => [i, el()]));
// Labels the script dims when the running engine ignores them. Each carries one field, which
// is what the script disables -- an engine-dependent control is exactly what needs covering.
const cascadeOnly = [...src.matchAll(/<label data-cascade-only>/g)].map(() => {
  const field = el("select");
  const label = el("label");
  label.children.push(field);
  label.querySelectorAll = () => [field];
  return label;
});
// The voice picker, dimmed when the engine that has voices to pick is not the one running.
const voiceOnly = [...src.matchAll(/<label data-voice-only[^>]*>/g)].map(() => {
  const label = el("label");
  label.children.push(byId.voice);
  label.querySelectorAll = () => [byId.voice];
  return label;
});
// options carry data- attributes too, so language switching must reach them
const dataEls = [...attrEls, ...Object.values(byId)];

const listeners = {};
const errors = [];
const sandbox = {
  document: {
    documentElement: { lang: "", dataset: {} },
    activeElement: null,
    getElementById: (i) => byId[i] ?? el(),
    querySelector: () => el(),
    querySelectorAll: (sel) => (sel === "[data-cascade-only]" ? cascadeOnly
      : sel === "[data-voice-only]" ? voiceOnly
      : sel === "[data-en]" ? dataEls
      : sel.startsWith("#langseg") ? [el("button"), el("button"), el("button")] : []),
    createElement: (t) => el(t),
    addEventListener() {},
  },
  Option: function (text, value) { const o = el("option"); o.text = text; o.value = value; return o; },
  localStorage: { getItem: () => null, setItem() {} },
  matchMedia: () => ({ matches: false }),          // OS prefers dark
  navigator: { language: "en-US" },
  // Everything but the device list is driven by hand below. /api/devices has to answer,
  // because the picker it fills is the one part of this page built from data the Mac
  // supplies at runtime -- and a stub that never answers leaves that branch unexecuted.
  fetch: (path) => path === "/api/devices"
    ? Promise.resolve({ ok: true, json: () => Promise.resolve(
        { devices: [{ name: "Jie\u2019s AirPods Pro", channels: 1 }], error: "" }) })
    : new Promise(() => {}),
  EventSource: class {
    addEventListener(name, fn) { listeners[name] = fn; }
    set onopen(fn) { listeners.open = fn; }
    set onerror(fn) { listeners.error = fn; }
  },
  setTimeout, clearTimeout, console,
};
sandbox.window = sandbox;

const vm = require("node:vm");
vm.createContext(sandbox);
try {
  vm.runInContext(script, sandbox, { filename: "admin.html" });
} catch (e) {
  errors.push(`on load: ${e.message}`);
}

// a status event exactly as bridge.py's Pipeline.status() builds it
const status = {
  state: "running", detail: "translating",
  config: { device: null, source: "en", target: "zh",
            model: "mlx-community/Qwen3-4B-Instruct-2507-4bit", stt: "mlx-audio-whisper",
            tts: "qwen3", voice: "zf_xiaoxiao", chat_size: 2, min_silence_ms: 64,
            lead_ms: 1500, engine: "cascade" },
  listeners: 3, level: 0.42, stoppable: true,
  url: "http://192.168.1.50:8000/",
};
for (const [name, data] of [["status", status], ["line", { kind: "out", text: "神爱世人", at: "10:31:02" }]]) {
  try {
    listeners[name]({ data: JSON.stringify(data) });
  } catch (e) {
    errors.push(`on ${name}: ${e.message}`);
  }
}

// the transcript must stay bounded: it is on screen for a whole sermon
for (let i = 0; i < 200; i++) {
  try {
    listeners.line({ data: JSON.stringify({ kind: "out", text: `line ${i}`, at: "10:00:00" }) });
  } catch (e) {
    errors.push(`on line ${i}: ${e.message}`);
    break;
  }
}
if (byId.log.children.length > 40) {
  errors.push(`transcript grew to ${byId.log.children.length} lines; it never trims`);
}

// a repaint must not overwrite the glossary the operator is part-way through editing
byId.glossary.value = "half-typed edit";
try { listeners.status({ data: JSON.stringify(status) }); } catch (e) { errors.push(e.message); }
if (byId.glossary.value !== "half-typed edit") {
  errors.push("a status update overwrote the glossary textarea");
}

// a status repaint must not relabel the option lists
const optionWrites = () => attrEls.filter((e) => e.tag === "option")
                                  .reduce((n, e) => n + e.writes, 0);
const before = optionWrites();
try {
  listeners.status({ data: JSON.stringify({ ...status, listeners: 9 }) });
} catch (e) {
  errors.push(`on second status: ${e.message}`);
}
if (optionWrites() !== before) {
  errors.push("a status update rewrote <option> text; that closes an open dropdown " +
              "and makes the selects unusable");
}

// every click on the theme button must change what is on screen
const themeClick = byId.themebtn.onclick;
if (typeof themeClick !== "function") {
  errors.push("the theme button has no handler");
} else {
  // compare what the page LOOKS like, not the attribute: no attribute renders as the
  // stylesheet's base theme, so "absent" and "dark" are the same picture
  const osLight = sandbox.matchMedia().matches;
  const appearance = () => sandbox.document.documentElement.dataset.theme
    ?? (osLight ? "light" : "dark");
  const seen = [appearance()];
  for (let i = 0; i < 4; i++) { themeClick(); seen.push(appearance()); }
  for (let i = 1; i < seen.length; i++) {
    if (seen[i] === seen[i - 1]) {
      errors.push(`theme click ${i} was a no-op: ${seen.join(" -> ")}`);
      break;
    }
  }
}

// The audio engine hears the sermon itself: no recogniser to give a language to, no
// translator to pick, no history. Those controls must go inert rather than sit there live.
if (cascadeOnly.length < 3) errors.push(`expected the cascade-only controls, found ${cascadeOnly.length}`);
if (cascadeOnly.some((l) => l.classList.contains("inert")))
  errors.push("cascade settings were dimmed while the cascade is running");
try {
  listeners.status({ data: JSON.stringify({ ...status, config: { ...status.config, engine: "omni" } }) });
} catch (e) {
  errors.push(`on the omni status: ${e.message}`);
}
if (!cascadeOnly.every((l) => l.classList.contains("inert")))
  errors.push("a setting the audio engine ignores was left live");
if (!cascadeOnly.every((l) => l.querySelectorAll()[0].disabled))
  errors.push("a setting the audio engine ignores was left editable");
if (!byId.enginehint.textContent) errors.push("nothing explained why those went grey");
// and back again: switching engines must restore them
listeners.status({ data: JSON.stringify(status) });
if (cascadeOnly.some((l) => l.classList.contains("inert")))
  errors.push("switching back to the cascade left its own settings dimmed");

// Stop has to be dead when there is nothing to stop, and the badge alone cannot say: a start
// that failed reads "error" with the pipeline already terminated, while a microphone that
// died mid-sermon reads "error" over a pipeline that is still up and still needs stopping.
// The bridge answers that question itself, in `stoppable`.
for (const [label, patch, dead] of [
  ["a running pipeline", {}, false],
  ["a stopped one", { state: "stopped", stoppable: false }, true],
  ["a start that failed", { state: "error", stoppable: false }, true],
  ["a start still loading", { state: "starting", stoppable: true }, false],
  ["a microphone that died mid-sermon", { state: "error", stoppable: true }, false],
]) {
  listeners.status({ data: JSON.stringify({ ...status, ...patch }) });
  if (byId.stopbtn.disabled !== dead)
    errors.push(`Stop was ${byId.stopbtn.disabled ? "dead" : "live"} for ${label}`);
}
listeners.status({ data: JSON.stringify(status) });      // running again, for what follows

// A Kokoro voice speaks one language, so the picker holds the target's voices and no
// others. Qwen3-TTS has a single voice of its own: nothing to pick, so the control goes grey.
const voiceOptions = () => (byId.voice.innerHTML.match(/value="[^"]+"/g) ?? [])
  .map((m) => m.slice(7, -1));
if (!voiceOnly.every((l) => l.classList.contains("inert")))
  errors.push("the voice picker stayed live under an engine with one voice");
listeners.status({ data: JSON.stringify({ ...status, config: { ...status.config, tts: "kokoro" } }) });
if (voiceOnly.some((l) => l.classList.contains("inert")))
  errors.push("the voice picker was dimmed while Kokoro was running");
if (!voiceOptions().every((v) => v.startsWith("z")) || voiceOptions().length !== 8)
  errors.push(`Chinese listeners were offered ${JSON.stringify(voiceOptions())}`);
listeners.status({ data: JSON.stringify({
  ...status, config: { ...status.config, tts: "kokoro", target: "en", voice: "af_heart" } }) });
if (!voiceOptions().every((v) => v.startsWith("a")) || voiceOptions().length !== 20)
  errors.push(`English listeners were offered ${JSON.stringify(voiceOptions())}`);
// ... and the other engine with voices offers its own names, not Kokoro's
listeners.status({ data: JSON.stringify({
  ...status, config: { ...status.config, tts: "piper", target: "en", voice: "en_US-ryan-medium" } }) });
if (!voiceOptions().every((v) => v.startsWith("en_US-")) || voiceOptions().length < 2)
  errors.push(`Piper listeners were offered ${JSON.stringify(voiceOptions())}`);
if (voiceOnly.some((l) => l.classList.contains("inert")))
  errors.push("the voice picker was dimmed while Piper was running");
if (!byId.voicehint.textContent) errors.push("nothing explained which voices are offered");

// Whisper re-transcribes a turn as the preacher keeps going; every pass carries the turn's
// id, and the console revises the line it is showing rather than stacking up four of them.
const nthLast = (n) => byId.log.children.at(-n).children[1].textContent;   // the log is capped
listeners.line({ data: JSON.stringify({ kind: "src", text: "死亡的原因", at: "10:41:00", id: 91 }) });
listeners.line({ data: JSON.stringify({ kind: "src", text: "死亡的原因是什么呢", at: "10:41:00", id: 91 }) });
if (nthLast(1) !== "死亡的原因是什么呢") errors.push(`the console did not revise: ${nthLast(1)}`);
if (nthLast(2) === "死亡的原因") errors.push("the console left the unfinished pass on screen");
listeners.line({ data: JSON.stringify({ kind: "out", text: "What causes death?", at: "10:41:03", id: 92 }) });
if (nthLast(1) !== "What causes death?" || nthLast(2) !== "死亡的原因是什么呢")
  errors.push("the translation did not start its own line");

// A translation is published when the voice reaches it, by which time the next sentence has
// been heard already. It goes under the line it translates, not at the bottom.
listeners.line({ data: JSON.stringify({ kind: "src", text: "死亡这个话题重大", at: "10:41:04", id: 93 }) });
listeners.line({ data: JSON.stringify({ kind: "src", text: "他就不能有智慧的活着", at: "10:41:05", id: 94 }) });
listeners.line({ data: JSON.stringify({ kind: "out", text: "The topic of death is significant", at: "10:41:06", id: 95, reply_to: 93 }) });
if (nthLast(2) !== "The topic of death is significant" || nthLast(1) !== "他就不能有智慧的活着")
  errors.push(`the console filed a translation under the wrong line: ${[nthLast(2), nthLast(1)]}`);
// a turn spoken in two sentences keeps them in the order they were said
listeners.line({ data: JSON.stringify({ kind: "out", text: "so some say", at: "10:41:07", id: 96, reply_to: 93 }) });
if (nthLast(2) !== "so some say" || nthLast(3) !== "The topic of death is significant")
  errors.push(`a turn's second sentence was filed out of order: ${[nthLast(3), nthLast(2), nthLast(1)]}`);

// Clearing removes the rows and their id cache. Otherwise a later revision with the same id
// updates a detached node and silently leaves the console showing "No transcript yet."
byId.clearlog.onclick();
listeners.line({ data: JSON.stringify({ kind: "src", text: "A revised line", at: "10:41:05", id: 91 }) });
if (byId.log.children.length !== 1 || byId.log.children[0].className === "empty")
  errors.push("a line whose old copy was cleared stayed hidden");

if (errors.length) {
  console.error("console script failed:\n  " + errors.join("\n  "));
  process.exit(1);
}

// it ran, but did it actually paint? These are the fields that went blank last time.
const shown = {
  url: byId.url.textContent, listeners: byId.listeners.textContent,
  detail: byId.detail.textContent, langhint: byId.langhint.textContent,
  level: byId.level.style.width, statetext: byId.statetext.textContent,
};
for (const [k, v] of Object.entries(shown)) {
  if (v === "" || v === undefined) { console.error(`#${k} was never filled in`); process.exit(1); }
}
if (byId.url.textContent !== status.url) { console.error("#url wrong"); process.exit(1); }
// The picker is filled from an async fetch, so it is only populated on the next tick.
// A headset paired after the page loaded has to be reachable without a reload.
setTimeout(() => {
  const opts = byId.device.children.map((o) => o.text);
  if (!opts.some((o) => String(o).includes("AirPods"))) {
    console.error(`the device picker was never filled: ${JSON.stringify(opts)}`);
    process.exit(1);
  }
  console.log(`ok: console script runs; painted ${Object.keys(shown).join(", ")};`
    + ` device picker filled from /api/devices (${opts.length} entries)`);
}, 0);
