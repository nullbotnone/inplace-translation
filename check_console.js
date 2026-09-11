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
    classList: { add() {}, remove() {}, contains: () => false },
    setAttribute(k, v) { this[k] = v; }, getAttribute(k) { return this[k]; },
    appendChild(c) { this.children.push(c); return c; },
    append(...c) { this.children.push(...c); },
    querySelector: () => null, querySelectorAll: () => [], closest: () => null,
    remove() {}, focus() {}, setSelectionRange() {}, addEventListener() {},
  };
  let text = "";
  e.writes = 0;
  Object.defineProperty(e, "textContent", {
    get: () => text,
    set(v) { text = v; e.writes++; },
  });
  return e;
}

const byId = Object.fromEntries(ids.map((i) => [i, el()]));
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
    querySelectorAll: (sel) => (sel === "[data-en]" ? dataEls
      : sel.startsWith("#langseg") ? [el("button"), el("button"), el("button")] : []),
    createElement: (t) => el(t),
    addEventListener() {},
  },
  localStorage: { getItem: () => null, setItem() {} },
  matchMedia: () => ({ matches: false }),          // OS prefers dark
  navigator: { language: "en-US" },
  fetch: () => new Promise(() => {}),          // never resolves: we drive state by hand
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
  config: { device: null, source: "en", target: "zh-Hans",
            model: "mlx-community/Qwen3-4B-Instruct-2507-4bit", stt: "mlx-audio-whisper",
            tts: "qwen3", chat_size: 2, min_silence_ms: 64 },
  listeners: 3, level: 0.42, glossary: "elder -> 长老",
  url: "http://192.168.1.50:8000/",
};
for (const [name, data] of [["status", status], ["line", { kind: "out", text: "神爱世人", at: "10:31:02" }]]) {
  try {
    listeners[name]({ data: JSON.stringify(data) });
  } catch (e) {
    errors.push(`on ${name}: ${e.message}`);
  }
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
console.log(`ok: console script runs; painted ${Object.keys(shown).join(", ")}`);
