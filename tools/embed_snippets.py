"""Embed pinned source snippets into the harness page and add a side drawer.

Usage:  python3 tools/embed_snippets.py index.html
Needs a local clone of robocurve/inspect-robots (or the fork) that contains
commit 7e4d1b7; point INSPECT_ROBOTS_REPO at it. Every <a class="src"> whose
href is a blob URL at that commit gets a data-snip attribute and its code is
embedded in the JSON at the bottom of the page. Safe to re-run after editing.
"""

from __future__ import annotations

import html
import json
import re
import subprocess
import sys

import os
REPO = os.environ.get("INSPECT_ROBOTS_REPO", "/Users/meow/work/HarnessVLA/inspect-robots")
SHA = "7e4d1b7aee1c0d3cfc3a05a7492b9d12cda666f9"
PAGE = sys.argv[1]
BASE = f"https://github.com/robocurve/inspect-robots/blob/{SHA}/"
MAX_LINES = 170

_cache: dict[str, list[str]] = {}


def file_lines(path: str) -> list[str] | None:
    if path not in _cache:
        r = subprocess.run(["git", "-C", REPO, "show", f"{SHA}:{path}"], capture_output=True, text=True)
        ok = r.returncode == 0 and not r.stdout.startswith("tree ")
        _cache[path] = r.stdout.splitlines() if ok else None  # type: ignore[assignment]
    return _cache[path]


def indent(s: str) -> int:
    return len(s) - len(s.lstrip(" "))


def find_end(lines: list[str], start: int) -> int:
    """1-based inclusive end of the block starting at 1-based ``start``."""
    i = start - 1
    first = lines[i]
    base = indent(first)
    stripped = first.strip()
    # module-level triple-quoted constant: end at the closing quotes
    if re.match(r"^[A-Z_]+\s*=\s*\"\"\"", stripped) and stripped.count('"""') == 1:
        for j in range(i + 1, len(lines)):
            if '"""' in lines[j]:
                return j + 1
    # parenthesised constant/tuple: end at matching close
    if re.match(r"^[A-Z_]+\s*(:[^=]+)?=\s*\($", stripped) or stripped.endswith("= ("):
        depth = 0
        for j in range(i, len(lines)):
            depth += lines[j].count("(") - lines[j].count(")")
            if depth <= 0 and j > i:
                return j + 1
    # decorated / def / class: walk until next line at indent <= base that starts a new statement
    end = i
    for j in range(i + 1, min(len(lines), i + MAX_LINES)):
        line = lines[j]
        if not line.strip():
            continue
        if indent(line) <= base and not line.lstrip().startswith(("#", ")", "]", "}", '"""', "'''")):
            if stripped.startswith("@") and lines[j].lstrip().startswith(("def ", "class ", "@")) and j == i + 1:
                continue
            break
        end = j
    # a bare statement line (no block) is just itself
    if stripped and not stripped.rstrip().endswith(":") and not stripped.startswith("@") and end == i:
        return i + 1
    return end + 1


def snippet_for(path: str, frag: str | None) -> dict | None:
    lines = file_lines(path)
    if lines is None:
        return None
    if frag:
        m = re.match(r"L(\d+)(?:-L(\d+))?$", frag)
        if not m:
            return None
        start = int(m.group(1))
        end = int(m.group(2)) if m.group(2) else find_end(lines, start)
        # a short single-line anchor into a block: widen to the enclosing block if tiny
        if end - start < 2 and not m.group(2):
            end = min(len(lines), start + 25)
    else:
        start, end = 1, min(len(lines), 60 if path.endswith(".py") else 90)
    end = min(end, start + MAX_LINES - 1, len(lines))
    code = "\n".join(lines[start - 1 : end])
    return {"path": path, "start": start, "end": end, "code": code}


page = open(PAGE, encoding="utf-8").read()
snips: dict[str, dict] = {}
counter = 0


def replace_anchor(m: re.Match[str]) -> str:
    global counter
    attrs, href = m.group(1), m.group(2)
    rest = href[len(BASE):]
    if "/tree/" in href or not rest:
        return m.group(0)
    path, _, frag = rest.partition("#")
    if frag and not frag.startswith("L"):
        frag = None  # markdown heading anchor
    snip = snippet_for(path, frag or None)
    if snip is None:
        return m.group(0)
    key = f"{path}#{snip['start']}-{snip['end']}"
    if key not in snips:
        counter += 1
        snip["id"] = f"s{counter}"
        snip["href"] = href
        snips[key] = snip
    sid = snips[key]["id"]
    if 'data-snip=' in attrs:
        return m.group(0)
    return f'<a{attrs} data-snip="{sid}" href="{href}"'


page = re.sub(r'<a([^>]*class="src"[^>]*?) href="(' + re.escape(BASE) + r'[^"]*)"', replace_anchor, page)
page = re.sub(r'<a([^>]*?) href="(' + re.escape(BASE) + r'[^"]*)"([^>]*class="src"[^>]*)>',
              lambda m: replace_anchor(re.match(r'<a([^>]*class="src"[^>]*?) href="([^"]*)"', f'<a{m.group(1)}{m.group(3)} href="{m.group(2)}"')) + ">" if False else m.group(0), page)

data = {v["id"]: {k: v[k] for k in ("path", "start", "end", "code", "href")} for v in snips.values()}
payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")

drawer_css = """
  /* ---- source drawer ---- */
  .drawer { position: fixed; top: 0; right: 0; bottom: 0; width: min(560px, 92vw); background: var(--panel); border-left: 1px solid var(--rule); box-shadow: -12px 0 32px -16px rgba(0,0,0,.35); transform: translateX(105%); transition: transform .2s ease; z-index: 50; display: flex; flex-direction: column; }
  .drawer[data-open="true"] { transform: none; }
  @media (prefers-reduced-motion: reduce) { .drawer { transition: none; } }
  .drawer header { display: flex; align-items: flex-start; gap: 10px; padding: 14px 16px 10px; border-bottom: 1px solid var(--rule); }
  .drawer header .titles { flex: 1; min-width: 0; }
  .drawer header .name { font-family: "Familjen Grotesk", "Noto Sans SC", sans-serif; font-weight: 700; font-size: 15px; }
  .drawer header .where { font-family: "JetBrains Mono", monospace; font-size: 12px; color: var(--ink-3); word-break: break-all; margin-top: 2px; }
  .drawer header .where a { border: 0; }
  .drawer header button { background: var(--bg-2); border: 1px solid var(--rule); color: var(--ink); border-radius: 6px; padding: 4px 10px; font: inherit; font-size: 13px; cursor: pointer; }
  .drawer header button:hover { border-color: var(--ink-3); }
  .drawer .code { flex: 1; overflow: auto; background: var(--code-bg); color: var(--code-ink); font-family: "JetBrains Mono", ui-monospace, Menlo, monospace; font-size: 12.5px; line-height: 1.55; padding: 12px 0 24px; }
  .drawer .code table { border-collapse: collapse; min-width: 100%; }
  .drawer .code td { padding: 0 14px 0 0; border: 0; vertical-align: top; white-space: pre; }
  .drawer .code td.ln { text-align: right; color: var(--code-cm); user-select: none; padding: 0 12px 0 14px; width: 1%; }
  .drawer .code .c { color: var(--code-cm); font-style: italic; }
  .drawer .code .s { color: var(--code-str); }
  .drawer .code .k { color: var(--code-kw); }
  .drawer .hint { font-size: 12.5px; color: var(--ink-3); padding: 8px 16px; border-top: 1px solid var(--rule); }
  .scrim { position: fixed; inset: 0; background: rgba(0,0,0,.25); z-index: 40; }
  .scrim[hidden] { display: none; }
  a.src[data-snip] { cursor: pointer; }
  a.src[data-snip]::after { content: " ▸"; font-size: 10px; }
"""

drawer_html = """
<div class="scrim" id="scrim" hidden></div>
<aside class="drawer" id="drawer" data-open="false" aria-label="源码片段">
  <header>
    <div class="titles"><div class="name" id="dname"></div><div class="where" id="dwhere"></div></div>
    <button type="button" id="dclose">关闭</button>
  </header>
  <div class="code" id="dcode"></div>
  <div class="hint">片段固定在上游提交 7e4d1b7。按 Esc 关闭；右上角可在 GitHub 打开完整文件。</div>
</aside>
<script id="snips" type="application/json">__PAYLOAD__</script>
<script>
(function () {
  var data = JSON.parse(document.getElementById('snips').textContent);
  var drawer = document.getElementById('drawer'), scrim = document.getElementById('scrim');
  var dname = document.getElementById('dname'), dwhere = document.getElementById('dwhere'), dcode = document.getElementById('dcode');
  function esc(s) { return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
  var KW = /\\b(def|class|return|if|elif|else|for|while|try|except|finally|with|as|import|from|raise|yield|lambda|and|or|not|in|is|None|True|False|pass|continue|break|assert|del|global|nonlocal|async|await)\\b/g;
  function hl(line, st) {
    // st.tq: inside a triple-quoted string; simple line-based highlighter, good enough for display
    var out = '', i = 0;
    if (st.tq) {
      var close = line.indexOf(st.tq);
      if (close < 0) { return { html: '<span class="s">' + esc(line) + '</span>', st: st }; }
      out += '<span class="s">' + esc(line.slice(0, close + 3)) + '</span>'; i = close + 3; st = { tq: null };
    }
    var rest = line.slice(i);
    var tqm = rest.match(/(\"\"\"|''')/);
    var hashAt = rest.indexOf('#');
    if (tqm && (hashAt < 0 || tqm.index < hashAt)) {
      var open = tqm.index, q = tqm[1];
      var after = rest.slice(open + 3), closeAt = after.indexOf(q);
      out += code(rest.slice(0, open));
      if (closeAt < 0) { out += '<span class="s">' + esc(rest.slice(open)) + '</span>'; return { html: out, st: { tq: q } }; }
      out += '<span class="s">' + esc(rest.slice(open, open + 3 + closeAt + 3)) + '</span>';
      var r2 = hl(rest.slice(open + 3 + closeAt + 3), { tq: null });
      return { html: out + r2.html, st: r2.st };
    }
    if (hashAt >= 0) { out += code(rest.slice(0, hashAt)) + '<span class="c">' + esc(rest.slice(hashAt)) + '</span>'; return { html: out, st: st }; }
    return { html: out + code(rest), st: st };
  }
  function code(s) {
    // strings then keywords
    var parts = s.split(/("(?:[^"\\\\]|\\\\.)*"|'(?:[^'\\\\]|\\\\.)*')/);
    return parts.map(function (p, idx) {
      if (idx % 2 === 1) return '<span class="s">' + esc(p) + '</span>';
      return esc(p).replace(KW, '<span class="k">$1</span>');
    }).join('');
  }
  function render(id) {
    var s = data[id]; if (!s) return;
    var isPy = /\\.py$/.test(s.path);
    dname.textContent = s.path.split('/').pop() + '  ·  L' + s.start + (s.end > s.start ? '–L' + s.end : '');
    dwhere.innerHTML = esc(s.path) + '  <a href="' + s.href + '" target="_blank" rel="noopener">在 GitHub 打开 ↗</a>';
    var rows = [], st = { tq: null }, lines = s.code.split('\\n');
    for (var i = 0; i < lines.length; i++) {
      var h = isPy ? hl(lines[i], st) : { html: esc(lines[i]), st: st }; st = h.st;
      rows.push('<tr><td class="ln">' + (s.start + i) + '</td><td>' + (h.html || ' ') + '</td></tr>');
    }
    dcode.innerHTML = '<table>' + rows.join('') + '</table>';
    dcode.scrollTop = 0;
    drawer.dataset.open = 'true'; scrim.hidden = false;
  }
  function close() { drawer.dataset.open = 'false'; scrim.hidden = true; }
  document.addEventListener('click', function (e) {
    var a = e.target.closest && e.target.closest('a.src[data-snip]');
    if (!a) return;
    if (e.metaKey || e.ctrlKey || e.shiftKey) return; // let modifier-clicks open GitHub
    e.preventDefault(); render(a.dataset.snip);
  });
  document.getElementById('dclose').addEventListener('click', close);
  scrim.addEventListener('click', close);
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') close(); });
})();
</script>
"""

# Idempotent: strip a previous drawer (CSS block and HTML tail) before re-adding.
page = re.sub(r"\n  /\* ---- source drawer ---- \*/.*?(?=\n</style>)", "", page, count=1, flags=re.S)
tail = ""
if "<!-- source drawer -->" in page:
    before, _, after = page.partition("<!-- source drawer -->")
    end = after.find("</script>\n", after.find("<script>"))
    tail = after[end + len("</script>\n"):] if end >= 0 else ""
    page = before.rstrip() + "\n"
elif "</body>" in page:
    page, _, tail = page.rpartition("</body>")
    tail = "</body>" + tail
    page = page.rstrip() + "\n"
page = page.replace("</style>", drawer_css + "</style>", 1)
page = page + "<!-- source drawer -->" + drawer_html.replace("__PAYLOAD__", payload) + tail
open(PAGE, "w", encoding="utf-8").write(page)
print(len(snips), "snippets;", sum(v["end"] - v["start"] + 1 for v in snips.values()), "lines;", len(payload) // 1024, "KB")
for v in sorted(snips.values(), key=lambda x: x["id"]):
    print(f"  {v['id']:>4} {v['path'].split('/')[-1]}:{v['start']}-{v['end']}")
