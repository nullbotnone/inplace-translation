"""Checks the HTML pages for the mistakes that fail silently: python3 check_pages.py

A missing translation or an undefined class doesn't throw, it just renders wrong
somewhere nobody is looking.
"""
import html.parser, re, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LANGS = ("zh", "tw", "en")
VOID = {"meta", "link", "br", "img", "input", "hr", "source", "area", "col", "option"}


def balanced(src, path):
    class P(html.parser.HTMLParser):
        def __init__(s): super().__init__(); s.stack = []; s.err = []
        def handle_startendtag(s, t, a): pass
        def handle_starttag(s, t, a):
            if t not in VOID: s.stack.append(t)
        def handle_endtag(s, t):
            if t in VOID: return
            if not s.stack: s.err.append(f"stray </{t}>")
            elif s.stack[-1] != t: s.err.append(f"</{t}> closes <{s.stack[-1]}>")
            else: s.stack.pop()
    p = P(); p.feed(src)
    assert not p.err, f"{path}: {p.err}"
    assert not p.stack, f"{path}: never closed {p.stack}"


def no_children_under_translation(src, path):
    """textContent on a node with children deletes them. Catching this statically because
    the page still renders: the label appears, the controls it replaced just vanish."""
    class P(html.parser.HTMLParser):
        def __init__(s): super().__init__(); s.depth = None; s.bad = []; s.stack = []
        def handle_starttag(s, tag, attrs):
            translated = any(k == "data-en" for k, _ in attrs)
            if s.depth is not None and tag not in VOID:
                s.bad.append(f"<{s.stack[s.depth]}> carries data-en but contains <{tag}>")
            if tag in VOID: return
            s.stack.append(tag)
            if translated and s.depth is None: s.depth = len(s.stack) - 1
        def handle_endtag(s, tag):
            if tag in VOID: return
            if s.stack:
                if s.depth == len(s.stack) - 1: s.depth = None
                s.stack.pop()
    p = P(); p.feed(src)
    assert not p.bad, f"{path}: {p.bad}"


def trilingual(src, path):
    for m in re.finditer(r'<[^>]*\bdata-en=', src):
        tag = src[m.start():src.index('>', m.start()) + 1]
        for l in LANGS:
            assert f'data-{l}=' in tag, f"{path}: no data-{l} on {tag[:80]}"
    return len(re.findall(r'data-en=', src))


def ids_resolve(src, path):
    """Both directions. A live id the script never touches is usually a dropped feature:
    the element still renders, empty, and nothing errors."""
    ids = set(re.findall(r'\bid="([^"]+)"', src))
    script = src[src.index('<script>'):]
    used = set(re.findall(r'\$\("([^"]+)"\)', src))
    assert not used - ids, f"{path}: JS looks up missing ids {sorted(used - ids)}"
    orphans = {i for i in ids if f'"{i}"' not in script and f'#{i}' not in script}
    assert not orphans, f"{path}: nothing in the script uses {sorted(orphans)}"


def classes_defined(src, css, path, extra=()):
    used = set()
    for m in re.finditer(r'class="([^"]+)"', src):
        used.update(m.group(1).split())
    defined = set(re.findall(r'\.([a-z][a-z0-9-]*)', css)) | set(extra)
    assert not used - defined, f"{path}: undefined classes {sorted(used - defined)}"


def themed(css, path):
    """Both themes must cover every colour, or the toggle half-applies."""
    dark = css[css.index(':root{') if ':root{' in css else css.index(':root {'):]
    light_at = dark.index('[data-theme="light"]')
    light = dark[light_at:dark.index('}', light_at)]
    dark = dark[:light_at]
    colour = {v for v in re.findall(r'(--[a-z0-9-]+):', dark) if not v.endswith(("mono", "display"))}
    missing = colour - set(re.findall(r'(--[a-z0-9-]+):', light))
    assert not missing, f"{path}: light theme missing {sorted(missing)}"
    return len(colour)


# ---- the operator console: trilingual, themed, self-contained
admin = (HERE / "admin.html").read_text()
balanced(admin, "admin.html")
ids_resolve(admin, "admin.html")
n_admin = trilingual(admin, "admin.html")
no_children_under_translation(admin, "admin.html")
n_tokens = themed(admin, "admin.html")
# every runtime string exists in all three languages, and every t() key exists
blocks = {l: re.search(rf'\n  {l}: \{{(.*?)\n  }},', admin, re.S).group(1) for l in LANGS}
keys = {l: set(re.findall(r'(\w+):\s*"', b)) for l, b in blocks.items()}
assert keys["zh"] == keys["tw"] == keys["en"], \
    f"console strings differ: {sorted(keys['zh'] ^ keys['en']) or sorted(keys['zh'] ^ keys['tw'])}"
looked_up = set(re.findall(r'(?<![A-Za-z_])t\("(\w+)"\)', admin))   # not createElement("div")
looked_up |= set(re.findall(r'toast\("(\w+)"\)', admin))
looked_up |= {"stopped", "starting", "running", "error"}      # t(s.state), from the server
assert not looked_up - keys["en"], f"console looks up undefined strings {sorted(looked_up - keys['en'])}"

# ---- the listener page: one language pair on purpose, both spelled out inline
listener = (HERE / "index.html").read_text()
balanced(listener, "index.html")

# ---- the public landing page
page = (HERE / "docs" / "index.html").read_text()
css = (HERE / "docs" / "styles.css").read_text()
balanced(page, "docs/index.html")
n_page = trilingual(page, "docs/index.html")
no_children_under_translation(page, "docs/index.html")
classes_defined(page, css, "docs/index.html", extra=("bx", "tx", "sb", "ln", "zone", "c"))
head = page[:page.index('</head>')]
for dep in ('href="/styles.css"', 'href="/favicon.svg"'):
    assert dep not in head, f"docs/index.html is not self-contained: {dep}"

# static checks cannot see a runtime error; run the script if node is available
import shutil, subprocess
if shutil.which("node"):
    r = subprocess.run(["node", str(HERE / "check_console.js")], capture_output=True, text=True)
    assert r.returncode == 0, "check_console.js: " + (r.stderr.strip() or r.stdout.strip())
    print(r.stdout.strip())
else:
    print("note: node not found, skipped the console runtime check")

print(f"ok: console {n_admin} labels + {len(keys['en'])} runtime strings x3 langs, "
      f"{n_tokens} themed tokens; landing page {n_page} labels")
