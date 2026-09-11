"""Check the landing page is self-contained and complete. python3 check_page.py"""
import html.parser, re, sys, pathlib
d = pathlib.Path(__file__).resolve().parent
src = (d / "index.html").read_text()
css = (d / "styles.css").read_text()
VAR = r'--[a-z0-9-]+'

# self-contained: nothing loaded from the parent site
head = src[:src.index('</head>')]
for bad in ['href="/styles.css"', 'href="/favicon.svg"', '<style>']:
    assert bad not in head, f"head references {bad}"
assert src.count('<style>') == 1, "only the SVG defs should carry a <style>"

# every class used is defined here
used = set()
for m in re.finditer(r'class="([^"]+)"', src):
    used.update(m.group(1).split())
defined = set(re.findall(r'\.([a-z][a-z0-9-]*)', css)) | {"bx", "tx", "sb", "ln", "zone", "c"}
assert not used - defined, f"undefined classes: {sorted(used - defined)}"

# every translatable node carries all three site languages
for m in re.finditer(r'<[^>]*\bdata-en=', src):
    tag = src[m.start():src.index('>', m.start()) + 1]
    assert 'data-zh=' in tag and 'data-tw=' in tag, "missing a language: " + tag[:90]

# balanced markup
VOID = {"meta", "link", "br", "img", "input", "hr", "source", "area", "col"}
class P(html.parser.HTMLParser):
    def __init__(s): super().__init__(); s.stack = []; s.err = []
    def handle_startendtag(s, t, a): pass
    def handle_starttag(s, t, a):
        if t not in VOID: s.stack.append(t)
    def handle_endtag(s, t):
        if not s.stack: s.err.append(f"stray </{t}>")
        elif s.stack[-1] != t: s.err.append(f"</{t}> closes <{s.stack[-1]}>")
        else: s.stack.pop()
p = P(); p.feed(src)
assert not p.err, p.err
assert not p.stack, f"never closed: {p.stack}"

# both themes cover every colour token, so the toggle can't half-apply
consumed = set(re.findall(rf'var\(({VAR})', css)) | set(re.findall(rf'var\(({VAR})', src))
dark  = set(re.findall(rf'({VAR}):', css[css.index(':root {'):css.index(':root[data-theme="light"]')]))
light = set(re.findall(rf'({VAR}):', css[css.index(':root[data-theme="light"]'):css.index('* { box-sizing')]))
assert not consumed - dark, f"undefined in dark: {sorted(consumed - dark)}"
colour = {"--bg", "--surface", "--surface-2", "--ink", "--ink-soft", "--line", "--line-strong",
          "--gold", "--accent", "--accent-strong", "--on-accent", "--shadow"}
assert not colour - light, f"light theme missing: {sorted(colour - light)}"

print(f"ok: {len(used)} classes defined, {len(re.findall(r'data-en=', src))} translated nodes, "
      f"{len(consumed)} tokens, light theme complete")
