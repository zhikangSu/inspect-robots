/* Reading annotations are display-only. Source strings and upstream lines stay intact. */
(() => {
  'use strict';
  const data = JSON.parse(document.getElementById('snips').textContent);
  const $ = id => document.getElementById(id);
  const drawer = $('drawer'), scrim = $('scrim'), body = $('drawer-body');
  let selected = null, annotated = true, opener = null, previousOverflow = '';
  const keywords = new Set('def class return if elif else for while try except finally with as import from raise yield lambda and or not in is None True False pass continue break assert del global nonlocal async await match case'.split(' '));
  const esc = value => String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  const span = (type, value) => `<span class="${type}">${esc(value)}</span>`;
  function highlight(line, state) {
    let out = '', i = 0;
    while (i < line.length) {
      if (state.triple) {
        const end = line.indexOf(state.triple, i);
        if (end < 0) return out + span('s', line.slice(i));
        out += span('s', line.slice(i, end + 3)); i = end + 3; state.triple = null; continue;
      }
      if (line[i] === '#') return out + span('c', line.slice(i));
      if (line[i] === '"' || line[i] === "'") {
        const quote = line[i];
        if (line.slice(i, i + 3) === quote.repeat(3)) {
          state.triple = quote.repeat(3); out += span('s', state.triple); i += 3; continue;
        }
        let end = i + 1;
        while (end < line.length) {
          if (line[end] === '\\') { end += 2; continue; }
          if (line[end++] === quote) break;
        }
        out += span('s', line.slice(i, end)); i = end; continue;
      }
      const identifier = /^[A-Za-z_][A-Za-z_0-9]*/.exec(line.slice(i));
      if (identifier) {
        const token = identifier[0]; out += keywords.has(token) ? span('k', token) : esc(token); i += token.length; continue;
      }
      out += esc(line[i++]);
    }
    return out;
  }
  function render() {
    const s = data[selected]; if (!s) return;
    const a = s.annotation;
    drawer.dataset.mode = annotated ? 'annotated' : 'raw';
    $('dannotated').setAttribute('aria-pressed', String(annotated));
    $('draw').setAttribute('aria-pressed', String(!annotated));
    $('dguide').hidden = !annotated;
    $('dname').textContent = `${s.path.split('/').pop()} · L${s.start}–L${s.end}`;
    $('dwhere').replaceChildren(document.createTextNode(s.path + '  '));
    const original = document.createElement('a');
    original.href = s.href; original.target = '_blank'; original.rel = 'noopener'; original.textContent = 'GitHub 原文 ↗';
    $('dwhere').append(original);
    $('dnote-count').textContent = `${a.blocks.length} 处中文注释`;
    $('dguide-title').textContent = a.title;
    $('dsummary').textContent = a.summary;
    $('dinputs').textContent = a.inputs;
    $('dreturns').textContent = a.returns;
    $('dexample').textContent = '举个例子：' + a.example;
    const docs = s.path.endsWith('.md');
    $('dinputs-label').textContent = docs ? '讨论对象' : '输入 / 依赖';
    $('dreturns-label').textContent = docs ? '要点 / 结论' : '产出 / 作用';
    const notes = new Map(a.blocks.map(n => [n.line, n.text]));
    const rows = [], state = {triple: null};
    s.code.split('\n').forEach((line, index) => {
      const number = s.start + index;
      if (annotated && notes.has(number)) rows.push(`<tr class="cn-note"><td class="note-symbol" aria-hidden="true">#</td><td class="note-text"><span class="note-label">中文解读 · 对应 L${number} 起的代码</span>${esc(notes.get(number))}</td></tr>`);
      const code = docs ? esc(line) : highlight(line, state);
      rows.push(`<tr data-source-line="${number}"><td class="ln">${number}</td><td class="source-line">${code}</td></tr>`);
    });
    $('dcode').innerHTML = '<table aria-label="带原始行号的源码节选"><colgroup><col><col></colgroup><tbody>' + rows.join('') + '</tbody></table>';
    body.scrollTop = 0;
  }
  function open(id, element) {
    if (!data[id]) return;
    const wasClosed = drawer.hidden;
    selected = id; annotated = true; opener = element || document.activeElement;
    render(); drawer.hidden = false; drawer.dataset.open = 'true'; scrim.hidden = false;
    if (wasClosed) { previousOverflow = document.body.style.overflow; document.body.style.overflow = 'hidden'; }
    $('dclose').focus({preventScroll: true});
  }
  function close() {
    if (drawer.hidden) return;
    drawer.hidden = true; drawer.dataset.open = 'false'; scrim.hidden = true;
    document.body.style.overflow = previousOverflow;
    opener?.focus({preventScroll: true});
  }
  document.addEventListener('click', event => {
    const a = event.target.closest?.('a.src[data-snip]');
    if (!a || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault(); open(a.dataset.snip, a);
  });
  $('dannotated').onclick = () => { annotated = true; render(); };
  $('draw').onclick = () => { annotated = false; render(); };
  $('dclose').onclick = close; scrim.onclick = close;
  drawer.addEventListener('keydown', event => {
    if (event.key === 'Escape') { event.preventDefault(); close(); }
    if (event.key !== 'Tab') return;
    const controls = [...drawer.querySelectorAll('a[href],button')].filter(el => !el.disabled && el.getClientRects().length);
    const first = controls[0], last = controls[controls.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  });
})();
