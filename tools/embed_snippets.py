"""Embed pinned source snippets into the harness page and add a side drawer.

Usage:  python3 tools/embed_snippets.py index.html
Needs a local clone of robocurve/inspect-robots (or the fork) that contains
commit 7e4d1b7; point INSPECT_ROBOTS_REPO at it. Every <a class="src"> whose
href is a blob URL at that commit gets a data-snip attribute and its code is
embedded in the JSON at the bottom of the page. Safe to re-run after editing.
"""

from __future__ import annotations

import html
import hashlib
from pathlib import Path
import json
import re
import subprocess
import sys

import os
SITE = Path(__file__).resolve().parents[1]
REPO = os.environ.get("INSPECT_ROBOTS_REPO", str(SITE))
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
    if frag and not frag.startswith("L") and path.endswith(".md"):
        # Honor a Markdown section link instead of silently showing the file intro.
        for index, line in enumerate(lines):
            heading = re.match(r"^(#{1,6}) (.+)$", line)
            if not heading:
                continue
            slug = re.sub(r"[^\w\- ]", "", heading[2].lower()).replace(" ", "-")
            if slug != frag:
                continue
            start = index + 1
            level = len(heading[1])
            end = len(lines)
            for following in range(index + 1, len(lines)):
                h = re.match(r"^(#{1,6}) ", lines[following])
                if h and len(h[1]) <= level:
                    end = following
                    break
            while end > start and not lines[end - 1].strip():
                end -= 1
            break
        else:
            raise ValueError(f"Markdown heading not found: {path}#{frag}")
    elif frag:
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


page = Path(PAGE).read_text(encoding="utf-8")
# Parse only authored HTML. Embedded snippets can themselves contain HTML examples.
if "<!-- source drawer -->" in page:
    before, _, after = page.partition("<!-- source drawer -->")
    end = after.find("</script>\n", after.find("<script>"))
    if end < 0:
        raise ValueError("Cannot locate the previous generated drawer script")
    tail = after[end + len("</script>\n"):]
    page = before.rstrip() + "\n"
else:
    page, closing, tail = page.rpartition("</body>")
    tail = closing + tail
page = re.sub(r"\n  /\* ---- source drawer ---- \*/.*?(?=\n</style>)", "", page, count=1, flags=re.S)

annotations = json.loads((SITE / "annotations/source.zh-CN.json").read_text())
if annotations["source_commit"] != SHA:
    raise ValueError("Chinese notes must target the same pinned commit as the snippets")
notes = annotations["snippets"]
snips = {}


def replace_anchor(match):
    tag = match[0]
    classes = re.search(r'class="([^"]*)"', tag)
    href_match = re.search(r'href="([^"]*)"', tag)
    if not classes or "src" not in classes[1].split() or not href_match:
        return tag
    href = html.unescape(href_match[1])
    if not href.startswith(BASE):
        return tag
    path, _, fragment = href[len(BASE):].partition("#")
    snippet = snippet_for(path, fragment or None)
    if snippet is None:
        kind = subprocess.run(["git", "-C", REPO, "cat-file", "-t", f"{SHA}:{path}"],
                              capture_output=True, text=True)
        if kind.returncode == 0 and kind.stdout.strip() == "tree":
            return tag.replace("/blob/", "/tree/", 1)
        raise ValueError(f"Pinned source unavailable: {href}. Set INSPECT_ROBOTS_REPO to a clone containing {SHA}.")
    key = f"{path}#{snippet['start']}-{snippet['end']}"
    if key not in snips:
        if key not in notes:
            raise ValueError(f"Missing Chinese explanation for {key}")
        annotation = notes[key]
        if annotation["code_sha256"] != hashlib.sha256(snippet["code"].encode()).hexdigest():
            raise ValueError(f"Source changed; review Chinese notes for {key}")
        for field in ("title", "summary", "inputs", "returns", "example"):
            if not annotation.get(field):
                raise ValueError(f"Empty {field} explanation for {key}")
        lines = snippet["code"].splitlines()
        seen = set()
        if not annotation["blocks"]:
            raise ValueError(f"No inline Chinese notes for {key}")
        for block in annotation["blocks"]:
            line = block["line"]
            if line in seen or not snippet["start"] <= line <= snippet["end"]:
                raise ValueError(f"Invalid annotation line {line} in {key}")
            if lines[line - snippet["start"]].strip() != block["anchor"]:
                raise ValueError(f"Annotation anchor mismatch: {key} at L{line}")
            seen.add(line)
        snippet.update(id=f"s{len(snips) + 1}", href=href, annotation=annotation)
        snips[key] = snippet
    tag = re.sub(r'\sdata-snip="[^"]*"', '', tag)
    return tag[:-1] + f' data-snip="{snips[key]["id"]}">'


page = re.sub(r"<a\b[^>]*>", replace_anchor, page)
if not snips:
    raise ValueError("No pinned code links found; refusing to publish an empty drawer")
data = {s["id"]: {k: v for k, v in s.items() if k != "id"} for s in snips.values()}
payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
css = (SITE / "assets/source-drawer.css").read_text()
js = (SITE / "assets/source-drawer.js").read_text()
page = re.sub(r"\s*</style>", lambda _: "\n  /* ---- source drawer ---- */\n" + css + "\n</style>", page, count=1)
drawer = """
<!-- source drawer -->
<div class="scrim" id="scrim" hidden></div>
<aside class="drawer" id="drawer" data-open="false" data-mode="annotated" role="dialog" aria-modal="true" aria-labelledby="dname" hidden>
  <header><div class="titles"><div class="name" id="dname"></div><div class="where" id="dwhere"></div></div><button type="button" id="dclose">关闭</button></header>
  <div class="modes" role="group" aria-label="源码显示方式"><button type="button" id="dannotated" aria-pressed="true">中文注释</button><button type="button" id="draw" aria-pressed="false">原始代码</button><span class="note-count" id="dnote-count"></span></div>
  <div class="drawer-body" id="drawer-body">
    <section class="guide" id="dguide"><h2 id="dguide-title"></h2><p id="dsummary"></p><dl><dt id="dinputs-label">输入 / 依赖</dt><dd id="dinputs"></dd><dt id="dreturns-label">产出 / 作用</dt><dd id="dreturns"></dd></dl><p class="example" id="dexample"></p></section>
    <div class="code" id="dcode"></div>
  </div>
  <div class="hint">中文解读由本站补充，# 行不属于上游源码。原代码与行号保持不变，固定于提交 7e4d1b7；片段可能不含完整函数。按 Esc 关闭。</div>
</aside>
<script id="snips" type="application/json">__PAYLOAD__</script>
<script>
__SCRIPT__
</script>
"""
page = page.rstrip() + "\n" + drawer.replace("__PAYLOAD__", payload).replace("__SCRIPT__", js) + tail
Path(PAGE).write_text(page, encoding="utf-8")
print(f"{len(snips)} snippets; {sum(len(s['annotation']['blocks']) for s in snips.values())} Chinese block notes; {len(payload.encode()) // 1024} KB embedded data")
