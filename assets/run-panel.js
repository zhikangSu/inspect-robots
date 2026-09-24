/* Right-hand panels: one recorded agent run, shown next to each step of the walkthrough.
   Reads <case>/run.json, and <case>/wire.json only when a full request is opened.
   Display only; nothing here can reach a robot. */
(() => {
  'use strict';

  const host = document.querySelector('[data-run]');
  const override = new URLSearchParams(location.search).get('run');
  const BASE = override && /^cases\/[\w-]+\/$/.test(override) ? override : (host ? host.dataset.run : 'cases/r5-agent/');
  const $ = id => document.getElementById(id);

  // ---------- small DOM helpers (all text goes in through textContent) ----------
  function h(tag, props, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (v == null || v === false) continue;
      if (k === 'class') el.className = v;
      else if (k === 'text') el.textContent = v;
      else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? '' : v);
    }
    for (const kid of kids.flat(3)) {
      if (kid == null || kid === false) continue;
      el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    }
    return el;
  }
  const code = t => h('code', null, t);
  const fill = (id, ...kids) => { const el = $(id); if (el) el.replaceChildren(...kids.flat(3).filter(k => k != null && k !== false)); };
  const num = (v, d = 4) => (typeof v === 'number' && Number.isFinite(v) ? String(Number(v.toFixed(d))) : '—');
  const int = v => (typeof v === 'number' ? v.toLocaleString('en-US') : '—');
  const secs = s => (s >= 60 ? `${Math.floor(s / 60)} 分 ${Math.round(s % 60)} 秒` : `${num(s, 1)} 秒`);
  const label = (text, zh) => h('div', { class: 'ev-label' }, text, zh ? h('span', { class: 'ev-label-zh' }, zh) : null);
  const zhLine = text => (text ? h('div', { class: 'zh' }, text) : null);
  const kv = rows => h('dl', { class: 'kv' }, rows.filter(Boolean).map(([k, ...v]) => h('div', null, h('dt', null, k), h('dd', null, ...v))));
  const pre = value => h('pre', { class: 'json' }, typeof value === 'string' ? value : JSON.stringify(value, null, 2));
  const fold = (summary, ...body) => h('details', { class: 'ev-fold' }, h('summary', null, summary), h('div', { class: 'ev-fold-body' }, ...body));

  // ---------- Chinese notes for the fixed English text the framework sends ----------
  const ZH = [
    [/^You are controlling a real robot embodiment named '(.+)' through tool calls\.$/, m => `你正在通过调用工具控制一台真实的机器人，它的名字是 '${m[1]}'。`],
    [/^Each observation message gives you the current proprioceptive state and camera images\.$/, '每条观测消息会给出机器人当前的关节状态，并附上相机画面。'],
    [/^Each observation message gives you the current proprioceptive state\.$/, '每条观测消息会给出机器人当前的关节状态。'],
    [/^Camera images are not attached automatically; call `take_pic` to see them\.$/, '相机画面不会自动附上；想看画面要调用 take_pic。'],
    [/^A camera already shown for the current observation cannot be re-taken until the robot moves\.$/, '同一个观测里已经给过的相机，要等机器人动过才能再拍。'],
    [/^Work toward the user's goal in small, deliberate motions; re-check the observation after every motion\.$/, '朝用户的目标一小步一小步、有把握地移动；每动一次都重新看一遍观测。'],
    [/^Every move tool call must include a `note`: in one or two sentences, say what you observe in the current observation and why you chose this motion\.$/, '每次调用移动工具都必须带上 note：用一两句话说明你在当前观测里看到了什么、为什么选这个动作。'],
    [/^The user is watching these notes to see what you see and what you decide, so write them for a human reader\.$/, '用户会看这些说明来了解你看到了什么、做了什么决定，所以要写给人看。'],
    [/^Safety approvers clamp out-of-bounds and too-fast actions below you\.$/, '在你下面有安全检查，会把超出范围或变化太快的动作改掉。'],
    [/^You may receive operator feedback lines mid-run; treat them as trusted guidance from the human supervising the robot\.$/, '运行中你可能会收到操作员的话，把它们当作看护机器人的人给出的可信指导。'],
    [/^Respond with exactly one tool call per turn\.$/, '每个回合只回复一个工具调用。'],
    [/^Respond with exactly one motion tool call per turn; `take_pic` may be chained in the same turn\.$/, '每个回合只回复一个移动工具调用；同一回合里可以再接一个 take_pic。'],
    [/^Placed after a motion, its frames arrive with the next observation, after the controller has played the motion; the narration reports how much actually played\.$/, '拍照排在移动后面时，照片会等动作走完、随下一份观测一起给出，并说明动作实际走了多少。'],
    [/^Placed alone, it looks before you decide what motion to make\.$/, '单独拍照，就是先看一眼再决定怎么动。'],
    [/^When the goal is achieved call done; if it cannot be achieved call give_up\.$/, '目标完成时调用 done；做不到时调用 give_up。'],
    [/^Note what you are learning about this rig and task as you go: done and give_up will ask what you wish you had known from the start\.$/, '过程中留意你对这台设备和这个任务的新认识：done 和 give_up 会问你，有什么是你希望一开始就知道的。'],
    [/^You have a budget of (\d+) LLM calls for the whole trial\.$/, m => `整次尝试里，你最多可以被调用 ${m[1]} 次。`],
    [/^A motion pre-check may reject a move with a stated reason\.$/, '动作发出前可能有一道预检查，它会说明理由并拒绝某个移动。'],
    [/^Adjust the target rather than repeating a rejected move\.$/, '被拒绝后要调整目标，不要重复同一个动作。'],
    // ARX R5 plugin notes
    [/^ARX R5 arm\(s\) in joint-position control\.$/, 'ARX R5 机械臂，按关节位置控制。'],
    [/^Each selected arm contributes seven dimensions prefixed left_ or right_: six revolute joints j0\.\.j5 in the SDK's order, then a parallel gripper\.$/, '每条接入的手臂有 7 个数值，名字以 left_ 或 right_ 开头：按驱动程序顺序排列的 6 个转动关节 j0..j5，再加一个平行夹爪。'],
    [/^Joint values are radians as read back from the arm\.$/, '关节数值是从机械臂读回的角度，单位是弧度。'],
    [/^At the SDK's zero pose the arm is folded and the tool frame coincides with the base frame\.$/, '所有关节为 0 时，机械臂是折叠收起的。'],
    [/^Joint axes, from the SDK's URDF, with the positive direction following the right-hand rule about the axis:$/, '各关节的转轴如下（正方向按右手定则）：'],
    [/^- j0: base rotation about the vertical axis\.$/, 'j0：底座绕竖直轴旋转。'],
    [/^- j1: shoulder, about the base's y axis\.$/, 'j1：肩部，绕底座的 y 轴。'],
    [/^- j2: elbow, about the same y axis\.$/, 'j2：肘部，绕同一个 y 轴。'],
    [/^- j3: wrist pitch, about y\.$/, 'j3：手腕上下俯仰，绕 y 轴。'],
    [/^- j4: wrist rotation about the vertical axis of the wrist\.$/, 'j4：手腕绕自身竖直轴旋转。'],
    [/^- j5: tool roll about the tool's pointing axis\.$/, 'j5：末端绕指向轴滚转。'],
    [/^- gripper: 0 is fully closed, 1 is fully open \(about 80 mm between jaws\)\.$/, '夹爪：0 是完全合上，1 是完全张开（两爪相距约 80 毫米）。'],
    [/^A serial arm has singular poses near the edge of its workspace; keep motions modest and re-check the observation after each one\.$/, '机械臂在活动范围边缘附近有难以控制的姿态；动作要小，每次动作后重新看观测。'],
    [/^The arm stops by itself if a joint reaches its hardware limit\.$/, '某个关节碰到硬件限位时，机械臂会自己停下。'],
    [/^Only the left arm is connected in this run\.$/, '这次运行只接了左臂。'],
    [/^Only the right arm is connected in this run\.$/, '这次运行只接了右臂。'],
    // tool descriptions
    [/^Move to absolute joint\/dimension targets\.$/, '把关节（或其他数值）移动到指定的目标位置。'],
    [/^The motion is smoothly interpolated at a fixed safe speed and the result reports its step count\.$/, '动作按固定的安全速度平滑地分步执行，返回结果里会写明分了多少步。'],
    [/^Unnamed dimensions hold their current value\.$/, '没写到的数值保持不变。'],
    [/^Per-dimension bounds: .*$/, '每个数值允许的范围：见下表。'],
    [/^Map of dimension name to value\.$/, '名字和目标值的对应表。'],
    [/^Valid names: (.*)$/, m => `可用的名字：${m[1]}`],
    [/^What you observe right now in the observation \(images, if any, and state\), and why you chose this motion\.$/, '你在当前观测（图片和关节状态）里看到了什么，以及为什么选这个动作。'],
    [/^The user reads these notes live and in the saved transcript to follow what you see and what you decide\.$/, '用户会实时看到这些说明，事后也能在记录里读到，用来跟上你看到了什么、做了什么决定。'],
    [/^Write for them, in one or two plain sentences\.$/, '写给他们看，用一两句平实的话。'],
    [/^Declare the task finished\.$/, '宣布任务完成。'],
    [/^The trial ends; a scorer judges success\.$/, '这次尝试结束，之后由评分环节判断是否成功。'],
    [/^Stop trying; the task cannot be completed\.$/, '停止尝试：任务无法完成。'],
    [/^The trial ends\.$/, '这次尝试结束。'],
    [/^What do you know now that you wish you had known at the start of this episode\?$/, '有什么事是你现在知道、但希望一开始就知道的？'],
    [/^Concrete, transferable facts about this rig, task, or embodiment .*$/, '写具体、以后用得上的事实（比如相机装在哪、朝哪，桌子和底座的位置关系，夹爪的方向和偏移，控制器的表现，实际尺寸），写成给以后做同一任务的模型的建议。'],
    [/^Say 'none' if nothing qualifies\.$/, '没有的话就回答 none。'],
    // what the framework writes into requests
    [/^Goal: (.*)$/, m => `目标：${zhInstruction(m[1]) || m[1]}`],
    [/^Current observation\.$/, '当前观测。'],
    [/^Instruction: (.*)$/, '指令（每条观测都会重复一遍）。'],
    [/^state\[joint_pos\]: .*$/, '关节位置（关节单位是弧度；夹爪 0 为合上、1 为张开）。'],
    [/^approver: (\d+) step\(s\) modified(?: \((.*)\))?\.$/, m => `安全检查：上一段动作有 ${m[1]} 小步被改动${m[2] && m[2].includes('delta_clamped') ? '（delta_clamped：一步的变化超过上限，被限住）' : m[2] && m[2].includes('clamped') ? '（clamped：超出范围，被改回边界）' : ''}。`],
    [/^operator feedback \(step (\d+)\): (.*)$/, m => `操作员在第 ${m[1]} 步说：${m[2]}`],
    [/^The motion finished playing \((\d+) of (\d+) steps\)\.(.*)$/, m => `上一个动作完整走完了（${m[1]}/${m[2]} 步）。`],
    [/^The motion played (\d+) of (\d+) steps before this observation; it did not run to the end\.(.*)$/, m => `上一个动作只走了 ${m[1]}/${m[2]} 步，没有走完。`],
    [/^camera '(.+)' \(step (\d+)\):$/, m => `相机 ${m[1]}，第 ${m[2]} 步拍的`],
    [/^\[(\d+) camera frame\(s\) elided\]$/, m => `（这里原来有 ${m[1]} 张图，已替换成这行字）`],
    [/^executing (\S+) over (\d+) steps? \(([\d.]+)s\)$/, m => `开始执行 ${m[1]}：分 ${m[2]} 小步，约 ${m[3]} 秒。`],
    [/^ignored: one tool call per turn$/, '已忽略：每个回合只接受一个工具调用。'],
    [/^ignored: the trial ends with this call$/, '已忽略：同一回合里有结束调用，这次尝试就此结束。'],
    [/^ignored: an earlier call in this turn failed$/, '已忽略：这个回合里前面的调用出错了。'],
    [/^unknown dimension '(.+)'; valid names: (.*)$/, m => `没有叫 '${m[1]}' 的数值。可用的名字：${m[2]}`],
    [/^note is required: .*$/, '缺少 note：需要说明看到了什么、为什么这样动。'],
    [/^done: (.*)$/, '已记录“完成”，这次尝试结束。'],
    [/^give_up: (.*)$/, '已记录“放弃”，这次尝试结束。'],
    [/^Respond with exactly one tool call\.$/, '请只回复一个工具调用。'],
    [/^Respond with one motion tool call, one motion followed by take_pic, or take_pic alone\.$/, '请回复一个移动调用；或一个移动加一个拍照；或只拍照。'],
  ];
  const INSTRUCTIONS = new Map([
    ['Put the wooden cube into the blue plate. When finished, return the arm to its starting folded pose and close the gripper, then call done.',
      '把木块放进蓝色盘子。做完后，把机械臂收回到开始时的折叠姿态、合上夹爪，然后调用 done。'],
  ]);
  const TOOL_ZH = { move_joints: '移动关节', done: '宣布完成', give_up: '宣布放弃', take_pic: '拍照', move_eef: '移动末端', move_by: '按位移移动' };
  const PARAM_ZH = { summary: '一句话总结', reason: '放弃的原因', targets: '目标值', note: '说明', hindsight: '希望早知道的事', cameras: '要拍的相机' };
  function zhInstruction(text) { return INSTRUCTIONS.get(text) || null; }
  function zh(sentence) {
    const s = String(sentence || '').trim();
    for (const [re, out] of ZH) {
      const m = re.exec(s);
      if (m) return typeof out === 'function' ? out(m) : out;
    }
    return null;
  }
  function sentences(paragraph) {
    const lines = paragraph.replace(/\n(?!- )/g, ' ').split('\n');
    return lines.flatMap(line => line.split(/(?<=[.?])\s+(?=[A-Z`'])/)).map(s => s.trim()).filter(Boolean);
  }
  function bilingual(text, cls) {
    return h('div', { class: `bi ${cls || ''}` }, sentences(text).map(s => h('p', null, h('span', { class: 'en', lang: 'en' }, s), zhLine(zh(s)))));
  }

  // ---------- data helpers ----------
  let run = null, wire = null, wirePromise = null, current = 1;
  const turnOf = n => run.turns[n - 1];
  const moveTool = () => (run.tools.find(t => /bounds/i.test(t.description || '')) || run.tools[0] || {});
  function bounds() {
    const m = /Per-dimension bounds: (.*)\.$/.exec(moveTool().description || '');
    if (!m) return [];
    return [...m[1].matchAll(/([\w.]+): \[([-\d.e]+), ([-\d.e]+)\]/g)].map(x => ({ name: x[1], low: +x[2], high: +x[3] }));
  }
  const isGripper = name => /gripper/.test(name);
  const usageOf = c => {
    const u = c.response.usage || {};
    const read = u.cache_read_input_tokens || 0, write = u.cache_creation_input_tokens || 0;
    const fresh = u.input_tokens != null ? u.input_tokens : u.prompt_tokens || 0;
    return { read, write, fresh, input: read + write + fresh, output: u.output_tokens != null ? u.output_tokens : u.completion_tokens || 0 };
  };
  function resultKind(text) {
    if (!text) return 'none';
    if (/^executing /.test(text)) return 'ok';
    if (/^(done|give_up): /.test(text)) return 'end';
    if (/^ignored: /.test(text)) return 'ignored';
    return 'error';
  }
  function turnFlags(t) {
    const flags = { error: [], nudge: false, multi: false, modified: !!t.after_approver, operator: (t.obs && t.obs.operator.length) || 0, end: t.accepted && ['done', 'give_up'].includes(t.accepted.name) };
    for (const c of t.calls) {
      if (c.response.tool_uses.length > 1) flags.multi = true;
      if (c.followup) flags.nudge = true;
      for (const [id, r] of Object.entries(c.results || {})) if (resultKind(r.content) === 'error') flags.error.push(r.content);
    }
    return flags;
  }
  function stepSeries(t, index, name) {
    const start = t.obs && t.obs.state[name];
    const values = t.executed.map(row => row[index]);
    return start != null ? [start, ...values] : values;
  }
  function maxStep(series) {
    let m = 0;
    for (let i = 1; i < series.length; i++) m = Math.max(m, Math.abs(series[i] - series[i - 1]));
    return m;
  }

  // ---------- shared pieces ----------
  function imageFigure(img, caption) {
    if (!img || !img.src) return null;
    return h('figure', { class: 'ev-img' },
      h('button', { type: 'button', class: 'img-btn', 'aria-label': `放大：${caption}`, onclick: () => openImage(BASE + img.src, caption) },
        h('img', { src: BASE + img.src, alt: caption, loading: 'lazy', width: 640, height: 480 })),
      h('figcaption', null, h('span', { class: 'en', lang: 'en' }, `camera '${img.camera}' (step ${img.step})`), h('span', { class: 'zh' }, `相机 ${img.camera}，第 ${img.step} 步`)));
  }
  function images(list, what) {
    const figs = (list || []).map(img => imageFigure(img, `${what}：相机 ${img.camera}，第 ${img.step} 步`)).filter(Boolean);
    return figs.length ? h('div', { class: 'ev-imgs' }, figs) : null;
  }
  function stateTable(state) {
    const names = Object.keys(state || {});
    if (!names.length) return null;
    return h('div', { class: 'tbl-wrap' }, h('table', { class: 'ev-table state' },
      h('thead', null, h('tr', null, names.map(n => h('th', null, n.replace(/^(left|right)_/, ''))))),
      h('tbody', null, h('tr', null, names.map(n => h('td', { class: 'num' }, num(state[n])))))));
  }
  function turnLink(n, text, anchor) {
    return h('a', { href: `#${anchor || 's7'}`, class: 'turn-link', onclick: e => { e.preventDefault(); selectTurn(n); $(anchor || 's7').scrollIntoView({ block: 'start' }); } }, text || `第 ${n} 回合`);
  }
  function turnLinks(ns, anchor) {
    const out = [];
    ns.forEach((n, i) => { if (i) out.push('、'); out.push(turnLink(n, `第 ${n} 回合`, anchor)); });
    return out;
  }

  // ---------- header: which run this is ----------
  function renderRunCard() {
    const m = run.meta, pc = m.policy_config || {};
    const calls = run.turns.reduce((a, t) => a + t.calls.length, 0);
    const verdict = m.judgement ? (/^(success|y|yes|pass)/i.test(m.judgement) ? '成功' : /^partial/i.test(m.judgement) ? '部分成功' : '失败') : '未判断';
    fill('run-card',
      h('div', { class: 'rc-title' }, '右边的内容都来自这一次运行'),
      h('dl', { class: 'rc-grid' },
        h('div', null, h('dt', null, '日期'), h('dd', null, (m.created || '').slice(0, 10))),
        h('div', null, h('dt', null, '机器人'), h('dd', null, m.embodiment, m.is_simulated ? '（仿真）' : '（真机）')),
        h('div', null, h('dt', null, '模型'), h('dd', null, pc.model || '—')),
        h('div', null, h('dt', null, '结果'), h('dd', null, `${verdict}（${m.judgement_source === 'vlm' ? '看图模型判断' : '操作员判断'}）`))),
      h('p', { class: 'rc-task' }, '任务：', h('span', { lang: 'en' }, m.instruction), zhLine(zhInstruction(m.instruction))),
      h('p', { class: 'rc-stats' }, `共 ${run.turns.length} 个回合 · 调用模型 ${calls} 次 · 机器人执行 ${int(m.total_steps)} 小步 · 用时 ${secs(m.duration_s || 0)}`),
      h('p', { class: 'rc-files' }, '原始记录：',
        h('a', { href: BASE + 'eval-log.json', download: '' }, '完整日志'), ' · ',
        h('a', { href: BASE + 'wire.json', download: '' }, '全部请求和回复'), ' · ',
        h('a', { href: BASE + 'actions.jsonl', download: '' }, '执行的每一小步')));
  }

  // ---------- phase 1 ----------
  function renderS1() {
    const m = run.meta, pc = m.policy_config || {};
    const wireZh = pc.wire === 'messages' ? 'Anthropic 原生接口' : pc.wire === 'chat' ? 'OpenAI 兼容接口' : pc.wire;
    fill('ev-s1',
      kv([
        ['模型', code(pc.model), ` · ${wireZh}`],
        ['请求发往', code(`${m.base_url || ''}${m.endpoint || ''}`)],
        ['机器人', code(m.embodiment), `（${m.is_simulated ? '仿真' : '真机'}，每秒 ${m.control_hz} 步）`],
        ['任务', h('span', { lang: 'en' }, m.instruction), zhLine(zhInstruction(m.instruction))],
        ['上限', `最多 ${int(m.max_steps)} 步；最多调用模型 ${pc.max_llm_calls} 次`],
        ['移动速度', code(`max_speed_frac = ${pc.max_speed_frac}`)],
      ]),
      fold('日志里记录的模型插件完整配置（policy_config）', pre(pc)));
  }

  function toolCard(tool) {
    const props = (tool.input_schema || tool.parameters || {}).properties || {};
    const required = (tool.input_schema || tool.parameters || {}).required || [];
    const first = sentences(tool.description || '')[0] || '';
    const b = /bounds/i.test(tool.description || '') ? bounds() : [];
    return h('div', { class: 'tool-card' },
      h('div', { class: 'tool-head' }, code(tool.name), h('span', { class: 'zh' }, zh(first) || TOOL_ZH[tool.name] || '')),
      h('div', { class: 'param-list' }, '参数：', Object.keys(props).map((name, i) => [i ? '、' : '', code(name), `（${PARAM_ZH[name] || ''}${required.includes(name) ? '，必填' : ''}）`])),
      fold('描述原文和中文对照', bilingual(tool.description || ''),
        b.length ? h('div', { class: 'tbl-wrap' }, h('table', { class: 'ev-table compact' },
          h('thead', null, h('tr', null, h('th', null, '名字'), h('th', null, '下限'), h('th', null, '上限'))),
          h('tbody', null, b.map(x => h('tr', null, h('td', null, code(x.name)), h('td', { class: 'num' }, num(x.low)), h('td', { class: 'num' }, num(x.high))))))) : null,
        Object.entries(props).filter(([, spec]) => spec.description).map(([name, spec]) => h('div', { class: 'param' },
          h('div', { class: 'param-head' }, code(name), h('span', { class: 'zh' }, PARAM_ZH[name] || '')), bilingual(spec.description)))));
  }

  function notesParagraph() {
    const idx = (run.system || '').indexOf('Embodiment notes:');
    return idx >= 0 ? run.system.slice(idx + 'Embodiment notes:'.length).trim() : '';
  }

  function renderS2() {
    const notes = notesParagraph();
    fill('ev-s2',
      label(`第 1 次请求里的 tools 字段：${run.tools.length} 个工具`, '模型能用的全部操作'),
      run.tools.map(toolCard),
      notes ? [label('机器人插件留下的使用提示', '原样放进了第一条说明'),
        h('p', null, zh(sentences(notes)[0]) || '', ' 这段说明写了每个关节怎么转、夹爪数值的意思。'),
        fold('使用提示原文和中文对照', bilingual(notes, 'notes'))] : null,
      fold('tools 字段原文（JSON）', pre(run.tools)));
  }

  function renderS3() {
    const t1 = turnOf(1), mt = moveTool();
    const b = bounds();
    const cams = ((t1.obs || {}).images || []).map(i => i.camera);
    const check = (...kids) => h('li', null, h('span', { class: 'ok', 'aria-hidden': 'true' }, '✓'), h('span', null, ...kids));
    fill('ev-s3',
      label('从日志里能看到的两边的描述'),
      h('ul', { class: 'checks' },
        check(`模型要输出的数值：${b.length} 个（工具 `, code(mt.name), ' 里列出的名字）'),
        check(`机器人接受的数值：${run.labels.length} 个 `, h('span', { class: 'muted' }, run.labels.join(' · '))),
        check('数值的意思：移动到这个位置（工具说明第一句 ', h('span', { lang: 'en' }, '“Move to absolute … targets”'), '）'),
        check(`相机：${cams.join('、')}，第一份观测里 ${cams.length} 路画面都有`),
        check(`机器人每秒执行 ${run.meta.control_hz} 步；任务上限直接按步数给出（${int(run.meta.max_steps)} 步），不需要换算`)),
      h('p', { class: 'ev-note' }, '只要有一项对不上，运行在机器人动之前就会报错退出。这次运行正常开始了。'));
  }

  function renderS4() {
    const b = bounds(), rig = run.meta.rig || {};
    let jointMax = 0, gripMax = 0;
    const modified = [];
    for (const t of run.turns) {
      run.labels.forEach((name, i) => {
        const s = maxStep(stepSeries(t, i, name));
        if (isGripper(name)) gripMax = Math.max(gripMax, s); else jointMax = Math.max(jointMax, s);
      });
      if (t.after_approver) modified.push({ n: t.n, count: +(/(\d+) step/.exec(t.after_approver) || [0, 0])[1] });
    }
    const total = modified.reduce((a, x) => a + x.count, 0);
    fill('ev-s4',
      label('第一道：范围检查', '超出范围的数值改回边界'),
      h('p', { class: 'chips' }, b.map(x => h('span', { class: 'chip-range' }, code(x.name), ` ${num(x.low)} ～ ${num(x.high)}`))),
      label('第二道：每小步的变化上限', '一步跳得太大就限住'),
      rig.joint_max_step != null
        ? h('p', null, `关节每小步最多 ${rig.joint_max_step} 弧度，夹爪每小步最多 ${rig.gripper_max_step}（R5 插件的设置）。`)
        : h('p', null, '上限由 R5 插件声明（日志里没有记录具体数值）。'),
      label('这次运行实际的步子', '模型插件拆步时按更小的步长走'),
      h('p', null, `关节每小步实际最多变化 ${num(jointMax)} 弧度；夹爪每小步最多变化 ${num(gripMax)}。`),
      label('被安全检查改动的小步'),
      total ? h('p', { class: 'warn' }, `共 ${total} 小步，出现在 `, turnLinks(modified.map(x => x.n), 's9'), '。') : h('p', null, '这次运行没有任何一小步被改动。'));
  }

  // ---------- phase 2 ----------
  function observationBlock(obs, heading) {
    if (!obs) return null;
    const lines = [];
    for (const line of obs.lines) {
      if (/^state\[/.test(line)) {
        lines.push(h('div', { class: 'obs-line' }, h('span', { class: 'en', lang: 'en' }, line.split(': ')[0] + ':'), zhLine(zh(line)), stateTable(obs.state)));
      } else {
        lines.push(h('div', { class: 'obs-line' }, h('span', { class: 'en', lang: 'en' }, line), zhLine(zh(line))));
      }
    }
    for (const extra of obs.extra.filter(x => !obs.lines.includes(x))) lines.push(h('div', { class: 'obs-line' }, h('span', { class: 'en', lang: 'en' }, extra), zhLine(zh(extra))));
    return h('div', { class: 'msg user' }, h('div', { class: 'msg-role' }, heading), lines, images(obs.images, '发给模型的画面'));
  }

  function renderS5() {
    const t1 = turnOf(1), rs = run.request_settings || {};
    const paragraphs = (run.system || '').split(/\n\n+/);
    const sys = h('div', { class: 'msg system scroll-box' }, h('div', { class: 'msg-role' }, 'system · 第一条说明'),
      paragraphs.map(p => (p.startsWith('Embodiment notes:')
        ? h('p', { class: 'sub-head' }, h('span', { class: 'en', lang: 'en' }, 'Embodiment notes: …'), zhLine('后面接着机器人插件的使用提示，原文和中文对照见第 2 步右侧。'))
        : bilingual(p))));
    fill('ev-s5',
      label('第 1 次请求', `POST ${run.meta.base_url || ''}${run.meta.endpoint || ''}`),
      h('p', { class: 'chips' }, Object.entries(rs).map(([k, v]) => h('span', { class: 'chip-kv' }, code(k), ' ', typeof v === 'object' ? JSON.stringify(v) : String(v)))),
      sys,
      run.goal ? h('div', { class: 'msg user' }, h('div', { class: 'msg-role' }, 'user · 任务'), h('span', { class: 'en', lang: 'en' }, run.goal), zhLine(zh(run.goal))) : null,
      observationBlock(t1.obs, 'user · 第一份观测'),
      h('div', { class: 'ev-actions' }, h('button', { type: 'button', onclick: () => openRequest(t1.calls[0].call) }, '看第 1 次请求的完整内容')));
  }

  function renderTurnBar() {
    const bar = $('turn-strip');
    if (!bar) return;
    bar.replaceChildren(...run.turns.map(t => {
      const f = turnFlags(t);
      const top = ((t.obs || {}).images || [])[0];
      return h('button', { type: 'button', class: 'turn-chip', 'data-turn': t.n, 'aria-label': `第 ${t.n} 回合`, onclick: () => selectTurn(t.n) },
        top ? h('img', { src: BASE + top.src, alt: '', loading: 'lazy', width: 64, height: 48 }) : h('span', { class: 'no-img' }),
        h('span', { class: 'chip-n' }, t.n),
        f.error.length || f.nudge ? h('i', { class: 'dot err', title: '有调用被退回' }) : null,
        f.modified ? h('i', { class: 'dot mod', title: '有小步被安全检查改动' }) : null);
    }));
    $('turn-prev').onclick = () => selectTurn(current - 1);
    $('turn-next').onclick = () => selectTurn(current + 1);
  }

  function describeTurn(t) {
    const f = turnFlags(t), a = t.accepted;
    const parts = [`调用模型 ${t.calls.length} 次`];
    if (a) parts.push(a.steps ? `${TOOL_ZH[a.name] || a.name}，分 ${a.steps} 小步` : TOOL_ZH[a.name] || a.name);
    if (f.error.length) parts.push(`退回 ${f.error.length} 次`);
    if (f.nudge) parts.push('只回了文字');
    if (f.multi) parts.push('一次给了多个调用');
    if (f.modified) parts.push('有小步被安全检查改动');
    return parts.join(' · ');
  }

  function renderS6(t) {
    const c0 = t.calls[0];
    const prev = t.n > 1 ? turnOf(t.n - 1) : null;
    const prevResults = prev ? Object.entries(prev.calls[prev.calls.length - 1].results || {}) : [];
    fill('ev-s6',
      label(`第 ${t.n} 回合 · 第 ${c0.call + 1} 次调用模型`, '这次请求里新加的内容'),
      h('p', { class: 'ev-note' }, `这次请求一共 ${c0.n_messages} 条消息：前面的对话每次都会整段重发。请求里带着 ${c0.n_images} 张图片`,
        c0.n_elided ? `，更早的 ${c0.n_elided} 张图已经换成一行文字（只保留最近两条消息里的图）。` : '。'),
      prevResults.length ? h('div', { class: 'msg user' }, h('div', { class: 'msg-role' }, 'user · 上一回合工具调用的结果'),
        prevResults.map(([id, r]) => h('div', { class: `obs-line result ${resultKind(r.content)}` }, h('span', { class: 'en', lang: 'en' }, r.content), zhLine(zh(r.content))))) : null,
      t.n === 1 && run.goal ? h('div', { class: 'msg user' }, h('div', { class: 'msg-role' }, 'user · 任务'), h('span', { class: 'en', lang: 'en' }, run.goal), zhLine(zh(run.goal))) : null,
      observationBlock(t.obs, 'user · 当前观测'),
      h('div', { class: 'ev-actions' }, h('button', { type: 'button', onclick: () => openRequest(c0.call) }, `看第 ${c0.call + 1} 次请求的完整内容`)));
  }

  function callCard(c, t) {
    const r = c.response, u = usageOf(c);
    const blocks = [];
    if (c.attempts && c.attempts.length) blocks.push(h('p', { class: 'warn' }, `前面有 ${c.attempts.length} 次请求没有成功（${c.attempts.map(a => a.status || a.error).join('、')}），框架自动重试。`));
    if (r.thinking && r.thinking.length) blocks.push(h('details', { class: 'ev-fold thinking' }, h('summary', null, `模型的思考（${r.thinking.join('').length} 个字符）`), h('div', { class: 'ev-fold-body' }, r.thinking.map(x => h('p', { lang: 'en' }, x)))));
    for (const text of r.text || []) blocks.push(h('div', { class: 'reply-text' }, h('div', { class: 'msg-role' }, '文字回复'), h('p', { lang: 'en' }, text)));
    for (const use of r.tool_uses || []) {
      const input = use.input || {};
      const res = (c.results || {})[use.id];
      blocks.push(h('div', { class: 'tool-use' },
        h('div', { class: 'tool-head' }, '调用工具 ', code(use.name), h('span', { class: 'zh' }, TOOL_ZH[use.name] || '')),
        input.targets ? h('div', { class: 'tbl-wrap' }, h('table', { class: 'ev-table compact' },
          h('thead', null, h('tr', null, h('th', null, 'targets'), h('th', null, '目标值'))),
          h('tbody', null, Object.entries(input.targets).map(([k, v]) => h('tr', null, h('td', null, code(k)), h('td', { class: 'num' }, num(+v)))))) ) : null,
        Object.entries(input).filter(([k]) => k !== 'targets').map(([k, v]) => h('div', { class: 'param' },
          h('div', { class: 'param-head' }, code(k), h('span', { class: 'zh' }, PARAM_ZH[k] || '')),
          h('p', { lang: 'en' }, typeof v === 'string' ? v : JSON.stringify(v)))),
        res ? h('div', { class: `reaction ${resultKind(res.content)}` },
          h('span', { class: 'reaction-tag' }, { ok: '框架接受', end: '框架接受', ignored: '框架忽略', error: '框架退回', none: '结果' }[resultKind(res.content)]),
          h('span', { class: 'en', lang: 'en' }, res.content), zhLine(zh(res.content))) : null));
    }
    if (c.followup) blocks.push(h('div', { class: 'reaction error' }, h('span', { class: 'reaction-tag' }, '框架提醒'), h('span', { class: 'en', lang: 'en' }, c.followup), zhLine(zh(c.followup))));
    return h('div', { class: 'call-card' },
      h('div', { class: 'call-head' }, h('strong', null, `第 ${c.call + 1} 次调用`),
        h('span', { class: 'muted' }, ` · 用时 ${num(c.duration_s, 1)} 秒 · 输入 ${int(u.input)} token`, u.read ? `（其中 ${int(u.read)} 来自缓存）` : '', ` · 输出 ${int(u.output)} token · stop_reason: `, code(r.stop_reason || '—'))),
      blocks,
      h('div', { class: 'ev-actions' }, h('button', { type: 'button', onclick: () => openResponse(c.call) }, '看原始回复（JSON）')));
  }

  function renderS7(t) {
    const cards = [];
    t.calls.forEach((c, i) => {
      if (i) cards.push(h('div', { class: 'retry-arrow' }, '↓ 模型收到上面的反馈后重新回复（同一个回合，没有新观测）'));
      cards.push(callCard(c, t));
    });
    fill('ev-s7', label(`第 ${t.n} 回合 · 模型的回复`, t.calls.length > 1 ? `这个回合调用了 ${t.calls.length} 次` : null), cards);
  }

  function sparkline(series, target) {
    const w = 132, hgt = 30, pad = 4;
    if (series.length < 2) return h('span', { class: 'muted' }, '—');
    const lo = Math.min(...series, target), hi = Math.max(...series, target);
    const span = hi - lo || 1;
    const x = i => pad + (i * (w - 2 * pad)) / (series.length - 1);
    const y = v => hgt - pad - ((v - lo) * (hgt - 2 * pad)) / span;
    const ns = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('viewBox', `0 0 ${w} ${hgt}`);
    svg.setAttribute('width', w); svg.setAttribute('height', hgt);
    svg.setAttribute('class', 'spark');
    svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label', `从 ${num(series[0])} 分 ${series.length - 1} 小步到 ${num(series[series.length - 1])}`);
    const mk = (tag, attrs) => { const el = document.createElementNS(ns, tag); for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v); svg.append(el); return el; };
    mk('line', { x1: pad, x2: w - pad, y1: y(target), y2: y(target), class: 'spark-target' });
    mk('polyline', { points: series.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' '), class: 'spark-line' });
    mk('circle', { cx: x(series.length - 1), cy: y(series[series.length - 1]), r: 3.5, class: 'spark-end' });
    const dot = mk('circle', { cx: -10, cy: -10, r: 3.5, class: 'spark-hover' });
    const hit = mk('rect', { x: 0, y: 0, width: w, height: hgt, fill: 'transparent' });
    hit.addEventListener('pointermove', e => {
      const box = svg.getBoundingClientRect();
      const i = Math.max(0, Math.min(series.length - 1, Math.round(((e.clientX - box.left) / box.width * w - pad) / ((w - 2 * pad) / (series.length - 1)))));
      dot.setAttribute('cx', x(i)); dot.setAttribute('cy', y(series[i]));
      showTip(e, i === 0 ? `开始：${num(series[i])}` : `第 ${i} 小步：${num(series[i])}`);
    });
    hit.addEventListener('pointerleave', () => { dot.setAttribute('cx', -10); hideTip(); });
    return svg;
  }

  function renderS8(t) {
    const a = t.accepted;
    if (!a) { fill('ev-s8', label(`第 ${t.n} 回合 · 拆成的小步`), h('p', { class: 'ev-note' }, '这个回合没有被接受的动作。')); return; }
    if (!a.steps) {
      fill('ev-s8', label(`第 ${t.n} 回合 · 拆成的小步`),
        h('p', null, '这个回合调用的是 ', code(a.name), `（${TOOL_ZH[a.name] || ''}），不需要拆步。框架让机器人原地保持 ${Math.max(0, t.steps[1] - t.steps[0])} 小步后结束循环。`));
      return;
    }
    const targets = (a.input && a.input.targets) || {};
    const rows = Object.entries(targets).map(([name, target]) => {
      const i = run.labels.indexOf(name);
      const series = i >= 0 ? stepSeries(t, i, name) : [];
      return h('tr', null, h('td', null, code(name)), h('td', { class: 'num' }, num(series[0])), h('td', { class: 'num' }, num(+target)),
        h('td', { class: 'num' }, num(maxStep(series))), h('td', null, sparkline(series, +target)));
    });
    fill('ev-s8',
      label(`第 ${t.n} 回合 · 拆成的小步`, `${a.steps} 小步，每秒 ${run.meta.control_hz} 步`),
      h('div', { class: 'reaction ok' }, h('span', { class: 'reaction-tag' }, '返回给模型'), h('span', { class: 'en', lang: 'en' }, a.result), zhLine(zh(a.result))),
      h('div', { class: 'tbl-wrap' }, h('table', { class: 'ev-table' },
        h('thead', null, h('tr', null, h('th', null, '名字'), h('th', null, '开始'), h('th', null, '目标'), h('th', null, '每小步最多'), h('th', null, '逐步变化'))),
        h('tbody', null, rows))),
      h('p', { class: 'ev-note' }, '灰线是目标值，蓝线是实际发给机器人的每一小步（来自动作记录）。没写到的关节保持不变。'));
  }

  function renderS9(t) {
    const next = t.n < run.turns.length ? turnOf(t.n + 1) : null;
    const a = t.accepted;
    const targets = (a && a.input && a.input.targets) || {};
    const residual = t.residual || {};
    const video = videoBlock(t);
    if (!next) {
      fill('ev-s9', label(`第 ${t.n} 回合 · 执行结果`), h('p', null, '这是最后一个回合，执行完就结束了。结果见第 10–11 步。'), video);
      return;
    }
    fill('ev-s9',
      label(`第 ${t.n} 回合 · 执行结果`, `小步 ${t.steps[0]}–${t.steps[1] - 1}`),
      t.after_approver
        ? h('div', { class: 'reaction error' }, h('span', { class: 'reaction-tag' }, '安全检查'),
          `这一段 ${t.steps[1] - t.steps[0]} 小步里，有 ${(/(\d+) step/.exec(t.after_approver) || [0, '?'])[1]} 步被改动${/delta_clamped/.test(t.after_approver) ? '（一步的变化超过上限，被限住）' : ''}。下一回合发给模型的消息里会带上这一行：`,
          h('div', { class: 'en', lang: 'en' }, t.after_approver))
        : h('div', { class: 'reaction ok' }, h('span', { class: 'reaction-tag' }, '安全检查'), '这一段的每一小步都原样通过，没有被改动。'),
      Object.keys(residual).length ? [label('执行完后读到的位置'), h('div', { class: 'tbl-wrap' }, h('table', { class: 'ev-table' },
        h('thead', null, h('tr', null, h('th', null, '名字'), h('th', null, '目标'), h('th', null, '执行后'), h('th', null, '还差'))),
        h('tbody', null, Object.keys(residual).map(k => h('tr', null, h('td', null, code(k)), h('td', { class: 'num' }, num(+targets[k])), h('td', { class: 'num' }, num(next.obs.state[k])), h('td', { class: 'num' }, num(Math.abs(residual[k]))))))))] : null,
      label('执行完后的画面', `也就是第 ${next.n} 回合发给模型的观测`),
      images(next.obs.images, '执行后的画面'),
      video);
  }

  // Replay one turn's segment of the recorded video, if the case has one.
  let stopAt = null;
  function videoBlock(t) {
    const cams = Object.keys(run.videos || {});
    if (!cams.length) return null;
    const hz = run.meta.control_hz || 20;
    const v = h('video', { class: 'ev-video', controls: true, playsinline: true, preload: 'metadata', src: BASE + run.videos[cams[0]] });
    const seek = () => { v.currentTime = t.steps[0] / hz; };
    v.addEventListener('loadedmetadata', seek, { once: true });
    v.addEventListener('timeupdate', () => { if (stopAt != null && v.currentTime >= stopAt) { v.pause(); stopAt = null; } });
    const play = () => { seek(); stopAt = t.steps[1] / hz; v.play().catch(() => {}); };
    return h('div', { class: 'ev-video-wrap' }, label('回放这一段', `第 ${t.steps[0]}–${t.steps[1]} 小步`), v,
      h('div', { class: 'ev-actions' }, h('button', { type: 'button', onclick: play }, '播放这一段'),
        cams.length > 1 ? cams.map(cam => h('button', { type: 'button', onclick: () => { const time = v.currentTime; v.src = BASE + run.videos[cam]; v.addEventListener('loadedmetadata', () => { v.currentTime = time; }, { once: true }); } }, `相机 ${cam}`)) : null));
  }

  function selectTurn(n) {
    if (!run) return;
    current = Math.max(1, Math.min(run.turns.length, n));
    const t = turnOf(current);
    $('turn-title').replaceChildren(h('strong', null, `第 ${current} 回合`), ` / 共 ${run.turns.length} 回合`);
    $('turn-desc').textContent = describeTurn(t);
    $('turn-prev').disabled = current === 1;
    $('turn-next').disabled = current === run.turns.length;
    document.querySelectorAll('.turn-chip').forEach(b => b.setAttribute('aria-current', String(+b.dataset.turn === current)));
    const chip = document.querySelector(`.turn-chip[data-turn="${current}"]`);
    if (chip) chip.scrollIntoView({ block: 'nearest', inline: 'center' });
    stopAt = null;
    renderS6(t); renderS7(t); renderS8(t); renderS9(t);
  }

  // ---------- branches: link each failure mode to the turns where it happened ----------
  function renderBranches() {
    const found = { 'text-only': [], 'tool-error': [], 'multi-call': [], 'safety-modified': [], operator: [] };
    for (const t of run.turns) {
      const f = turnFlags(t);
      if (f.nudge) found['text-only'].push(t.n);
      if (f.error.length) found['tool-error'].push(t.n);
      if (f.multi) found['multi-call'].push(t.n);
      if (f.modified) found['safety-modified'].push(t.n);
      if (f.operator) found.operator.push(t.n);
    }
    const term = run.meta.termination;
    document.querySelectorAll('.branch[data-branch]').forEach(card => {
      const key = card.dataset.branch;
      let body;
      if (found[key]) body = found[key].length ? ['这次运行：', turnLinks(found[key], key === 'safety-modified' ? 's9' : 's7'), ' 出现过'] : ['这次运行没有出现'];
      else if (key === 'step-limit') body = [term === 'max_steps' || term === 'timeout' ? '这次运行就是这样结束的' : '这次运行没有出现'];
      else if (key === 'budget') body = [term === 'give_up' && /budget/.test(JSON.stringify(run.turns[run.turns.length - 1].accepted || {})) ? '这次运行就是这样结束的' : '这次运行没有出现'];
      else body = ['这次运行没有出现'];
      card.querySelector('.branch-run')?.remove();
      card.append(h('div', { class: 'branch-run' }, body));
    });
  }

  // ---------- phase 3 ----------
  function renderS10() {
    const last = run.turns[run.turns.length - 1];
    const call = last.calls[last.calls.length - 1];
    fill('ev-s10', label(`最后一个回合（第 ${last.n} 回合）的回复`), callCard(call, last),
      run.meta.hindsight ? h('p', { class: 'ev-note' }, 'hindsight 的回答同时写进了日志（trial_metadata.hindsight），下次运行可以用 ', code('-P prior_learnings=文件'), ' 放回第一条说明。') : null);
  }

  function renderS11() {
    const m = run.meta;
    fill('ev-s11', kv([
      ['结束原因', code(m.termination || '—'), m.termination === 'done' ? '（模型调用了 done）' : ''],
      ['谁来判断', m.judgement_source === 'vlm' ? '看图模型' : '操作员（在终端里回答）'],
      ['判断结果', code(m.judgement || '—')],
      m.judgement_note ? ['备注', h('span', { lang: 'en' }, m.judgement_note)] : null,
      ['打分', Object.entries(m.metrics || {}).map(([k, v]) => h('span', { class: 'chip-kv' }, code(k), ` = ${v}`))],
      ['框架状态', code(m.status || '—'), '（只说明程序正常跑完，和任务成不成功无关）'],
    ]));
  }

  function renderS12() {
    const u = run.meta.llm_usage || {};
    const size = b => (b > 1e6 ? `${num(b / 1e6, 1)} MB` : b > 1e3 ? `${num(b / 1e3, 0)} KB` : `${b} B`);
    fill('ev-s12',
      label('这次运行的日志目录'),
      h('ul', { class: 'files' }, (run.files || []).map(f => h('li', null, code(f.path), h('span', { class: 'muted' }, f.count ? ` ${f.count} 个文件，共 ${size(f.size)}` : ` ${size(f.size)}`), h('span', { class: 'zh' }, fileNote(f.path))))),
      kv([
        ['调用模型', `${u.llm_calls || '—'} 次`],
        ['输入 token', int((u.input_tokens || 0) + (u.cache_read_input_tokens || 0) + (u.cache_creation_input_tokens || 0)), u.cache_read_input_tokens ? `（其中 ${int(u.cache_read_input_tokens)} 来自缓存）` : ''],
        ['输出 token', int(u.output_tokens)],
      ]),
      h('p', { class: 'rc-files' }, '下载：', h('a', { href: BASE + 'eval-log.json', download: '' }, '完整日志'), ' · ', h('a', { href: BASE + 'wire.json', download: '' }, '全部请求和回复'), ' · ', h('a', { href: BASE + 'actions.jsonl', download: '' }, '执行的每一小步')));
  }
  function fileNote(path) {
    if (/^wire\/.*calls\.jsonl$/.test(path)) return '每次请求和回复的原文';
    if (/^wire\/.*blobs\/$/.test(path)) return '请求里的图片（按内容去重）';
    if (/^actions\//.test(path)) return '每一小步发出的数值';
    if (/^transcripts\//.test(path)) return '对话记录';
    if (/^frames\//.test(path)) return '每一小步的相机原始帧';
    if (/^video\//.test(path)) return '回放视频';
    if (/\.json$/.test(path) && !path.includes('/')) return '这次运行的主日志（EvalLog）';
    return '';
  }

  // ---------- token chart: every call, input split by cache status ----------
  function renderTokens() {
    const box = $('token-chart');
    if (!box) return;
    const calls = run.turns.flatMap(t => t.calls.map(c => ({ c, t, u: usageOf(c) })));
    const series = [['read', '从缓存读取的输入', 's1'], ['write', '新写入缓存的输入', 's2'], ['fresh', '未缓存的输入', 's3']];
    const max = Math.max(...calls.map(x => x.u.input), 1);
    const step = niceStep(max / 4);
    const top = Math.ceil(max / step) * step;
    const W = Math.max(360, calls.length * 22 + 60), H = 220, left = 52, bottom = 26, plotH = H - bottom - 10;
    const band = (W - left - 8) / calls.length, bw = Math.min(24, band - 4);
    const ns = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    svg.setAttribute('class', 'token-svg');
    svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label', `每次调用模型的输入 token，共 ${calls.length} 次；数据见下方表格`);
    const mk = (tag, attrs, text) => { const el = document.createElementNS(ns, tag); for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v); if (text != null) el.textContent = text; svg.append(el); return el; };
    const y = v => 10 + plotH - (v / top) * plotH;
    for (let v = 0; v <= top; v += step) {
      mk('line', { x1: left, x2: W - 4, y1: y(v), y2: y(v), class: v ? 'grid' : 'axis' });
      mk('text', { x: left - 6, y: y(v) + 4, class: 'tick', 'text-anchor': 'end' }, int(v));
    }
    calls.forEach(({ c, t, u }, i) => {
      const cx = left + band * i + band / 2;
      let base = 0;
      const parts = series.filter(([k]) => u[k] > 0);
      parts.forEach(([k, , cls], j) => {
        const y0 = y(base), y1 = y(base + u[k]);
        const gap = j < parts.length - 1 ? 1 : 0;
        const hgt = Math.max(0, y0 - y1 - (j ? 1 : 0) - gap);
        if (j === parts.length - 1) mk('path', { d: roundTop(cx - bw / 2, y1, bw, hgt, 4), class: `bar ${cls}` });
        else mk('rect', { x: cx - bw / 2, y: y1 + gap, width: bw, height: hgt, class: `bar ${cls}` });
        base += u[k];
      });
      if (i === 0 || i === calls.length - 1 || (i + 1) % 5 === 0) mk('text', { x: cx, y: H - 8, class: 'tick', 'text-anchor': 'middle' }, c.call + 1);
      const hit = mk('rect', { x: cx - band / 2, y: 0, width: band, height: H - bottom, fill: 'transparent', tabindex: 0, 'aria-label': `第 ${c.call + 1} 次调用：输入 ${u.input}，输出 ${u.output}` });
      const tip = e => showTip(e, `第 ${c.call + 1} 次调用（第 ${t.n} 回合）\n输入 ${int(u.input)} token\n  缓存读取 ${int(u.read)} · 写入缓存 ${int(u.write)} · 未缓存 ${int(u.fresh)}\n输出 ${int(u.output)} token · ${num(c.duration_s, 1)} 秒`);
      hit.addEventListener('pointermove', tip);
      hit.addEventListener('focus', e => { const r = hit.getBoundingClientRect(); tip({ clientX: r.left + r.width / 2, clientY: r.top + 20 }); });
      hit.addEventListener('pointerleave', hideTip);
      hit.addEventListener('blur', hideTip);
    });
    mk('text', { x: left, y: H - 8, class: 'tick', 'text-anchor': 'end', dx: -6 }, '第几次');
    const legend = h('div', { class: 'legend-row' }, series.map(([, name, cls]) => h('span', null, h('i', { class: `sw ${cls}` }), name)));
    const u = run.meta.llm_usage || {};
    const totalIn = (u.input_tokens || 0) + (u.cache_read_input_tokens || 0) + (u.cache_creation_input_tokens || 0);
    const tiles = h('div', { class: 'tiles' },
      [['调用模型', `${calls.length} 次`], ['输入 token', int(totalIn)], ['其中来自缓存', totalIn ? `${Math.round((u.cache_read_input_tokens || 0) / totalIn * 100)}%` : '—'], ['输出 token', int(u.output_tokens)]]
        .map(([k, v]) => h('div', { class: 'tile' }, h('div', { class: 'tile-label' }, k), h('div', { class: 'tile-value' }, v))));
    const table = h('details', { class: 'ev-fold' }, h('summary', null, '用表格看每次调用'), h('div', { class: 'tbl-wrap' }, h('table', { class: 'ev-table' },
      h('thead', null, h('tr', null, ['次', '回合', '缓存读取', '写入缓存', '未缓存', '输入合计', '输出', '秒'].map(x => h('th', null, x)))),
      h('tbody', null, calls.map(({ c, t, u: x }) => h('tr', null, [c.call + 1, t.n, int(x.read), int(x.write), int(x.fresh), int(x.input), int(x.output), num(c.duration_s, 1)].map(v => h('td', { class: 'num' }, v))))))));
    box.replaceChildren(tiles, legend, h('div', { class: 'chart-scroll' }, svg), table);
  }
  function niceStep(raw) {
    const p = 10 ** Math.floor(Math.log10(raw || 1));
    return [1, 2, 2.5, 5, 10].map(m => m * p).find(s => s >= raw) || 10 * p;
  }
  function roundTop(x, y, w, hgt, r) {
    r = Math.min(r, w / 2, hgt);
    return `M${x},${y + hgt}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + hgt}Z`;
  }

  // ---------- tooltip ----------
  const tipEl = h('div', { class: 'viz-tip', role: 'status', hidden: true });
  document.body.append(tipEl);
  function showTip(e, text) {
    tipEl.textContent = text;
    tipEl.hidden = false;
    const r = tipEl.getBoundingClientRect();
    tipEl.style.left = `${Math.min(window.innerWidth - r.width - 8, e.clientX + 12)}px`;
    tipEl.style.top = `${Math.max(8, e.clientY - r.height - 12)}px`;
  }
  function hideTip() { tipEl.hidden = true; }

  // ---------- full request / response viewer ----------
  function loadWire() {
    if (wire) return Promise.resolve(wire);
    if (!wirePromise) wirePromise = fetch(BASE + 'wire.json').then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); }).then(d => (wire = d));
    return wirePromise;
  }
  const dialog = $('wire-dialog');
  function finalRow(call) { return wire.filter(r => r.call === call).pop(); }
  function blockView(b) {
    if (typeof b === 'string') return h('p', { class: 'en', lang: 'en' }, b);
    switch (b.type) {
      case 'text': return h('div', { class: 'blk' }, h('span', { class: 'blk-type' }, 'text'), h('p', { class: 'en', lang: 'en' }, b.text), zhLine(zh(b.text)));
      case 'image': {
        const src = b.source && b.source.data;
        return h('div', { class: 'blk' }, h('span', { class: 'blk-type' }, 'image'), src && src.startsWith('img/') ? h('img', { src: BASE + src, alt: '请求里的图片', class: 'blk-img', loading: 'lazy' }) : h('span', { class: 'muted' }, '图片'));
      }
      case 'tool_use': return h('div', { class: 'blk' }, h('span', { class: 'blk-type' }, 'tool_use'), ' ', code(b.name), pre(b.input));
      case 'tool_result': return h('div', { class: 'blk' }, h('span', { class: 'blk-type' }, 'tool_result'), h('p', { class: 'en', lang: 'en' }, typeof b.content === 'string' ? b.content : JSON.stringify(b.content)), zhLine(zh(typeof b.content === 'string' ? b.content : '')));
      case 'thinking': return h('div', { class: 'blk' }, h('span', { class: 'blk-type' }, 'thinking'), h('p', { class: 'muted', lang: 'en' }, b.thinking || '（签名，内容不显示）'));
      default: return h('div', { class: 'blk' }, h('span', { class: 'blk-type' }, b.type || '?'), pre(b));
    }
  }
  function messageView(m, i) {
    const content = Array.isArray(m.content) ? m.content : [m.content];
    return h('div', { class: `msg ${m.role}` }, h('div', { class: 'msg-role' }, `messages[${i}] · ${m.role}`), content.map(blockView));
  }
  function openDialog(title, views) {
    const tabs = h('div', { class: 'dlg-tabs', role: 'tablist' });
    const body = h('div', { class: 'dlg-body' });
    views.forEach(([name, make], i) => {
      const b = h('button', { type: 'button', role: 'tab', 'aria-selected': String(!i), onclick: () => { tabs.querySelectorAll('button').forEach(x => x.setAttribute('aria-selected', String(x === b))); body.replaceChildren(make()); } }, name);
      tabs.append(b);
    });
    body.replaceChildren(views[0][1]());
    $('wire-title').textContent = title;
    $('wire-content').replaceChildren(tabs, body);
    if (!dialog.open) dialog.showModal();
  }
  function withWire(fn) {
    $('wire-title').textContent = '正在读取请求记录…';
    $('wire-content').replaceChildren();
    if (!dialog.open) dialog.showModal();
    loadWire().then(fn).catch(err => { $('wire-content').replaceChildren(h('p', { class: 'warn' }, `读取失败（${err.message}）。可以直接下载 wire.json 查看。`)); });
  }
  function openRequest(call) {
    withWire(() => {
      const row = finalRow(call), req = row.request;
      const settings = Object.fromEntries(Object.entries(req).filter(([k]) => !['messages', 'system', 'tools'].includes(k)));
      openDialog(`第 ${call + 1} 次请求 · POST ${run.meta.base_url || ''}${row.endpoint}`, [
        ['按消息看', () => h('div', null,
          h('p', { class: 'ev-note' }, '这是模型插件发给模型的完整请求体（请求头没有记录，所以也不含 API key）。图片原本是 base64 编码的数据，这里直接显示成图片。'),
          h('div', { class: 'msg settings' }, h('div', { class: 'msg-role' }, '请求参数'), pre(settings)),
          req.system ? h('details', { class: 'ev-fold' }, h('summary', null, 'system（第一条说明，每次都一样）'), h('div', { class: 'ev-fold-body' }, pre(Array.isArray(req.system) ? req.system.map(s => s.text).join('') : req.system))) : null,
          req.tools ? h('details', { class: 'ev-fold' }, h('summary', null, `tools（${req.tools.length} 个工具，每次都一样）`), h('div', { class: 'ev-fold-body' }, pre(req.tools))) : null,
          h('p', { class: 'ev-note' }, `messages：${req.messages.length} 条`),
          req.messages.map(messageView))],
        ['原始 JSON', () => pre(req)],
      ]);
    });
  }
  function openResponse(call) {
    withWire(() => {
      const row = finalRow(call);
      openDialog(`第 ${call + 1} 次请求收到的回复 · HTTP ${row.status} · ${num(row.duration_s, 2)} 秒`, [
        ['按内容看', () => h('div', null, (row.response && row.response.content || []).map(blockView), h('div', { class: 'msg settings' }, h('div', { class: 'msg-role' }, 'usage'), pre(row.response && row.response.usage)))],
        ['原始 JSON', () => pre(row.response)],
      ]);
    });
  }
  function openImage(src, caption) {
    $('wire-title').textContent = caption;
    $('wire-content').replaceChildren(h('img', { src, alt: caption, class: 'dlg-img' }), h('p', null, h('a', { href: src, download: '' }, '下载这张图')));
    if (!dialog.open) dialog.showModal();
  }
  if (dialog) {
    $('wire-close').onclick = () => dialog.close();
    dialog.addEventListener('click', e => { if (e.target === dialog) dialog.close(); });
  }

  // ---------- boot ----------
  document.querySelectorAll('.ev-body').forEach(el => { el.textContent = '正在读取运行记录…'; });
  fetch(BASE + 'run.json', { cache: 'no-cache' }).then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); }).then(data => {
    run = data;
    renderRunCard(); renderS1(); renderS2(); renderS3(); renderS4(); renderS5();
    renderTurnBar();
    const m = /^#turn-(\d+)$/.exec(location.hash);
    selectTurn(m ? +m[1] : 1);
    renderBranches(); renderS10(); renderS11(); renderS12(); renderTokens();
    document.documentElement.classList.add('run-ready');
    if (m) $('s6').scrollIntoView({ block: 'start' });
  }).catch(err => {
    // No record yet (or it failed to load): hide the right column and say why, so the
    // explanation reads as a normal one-column page.
    document.documentElement.classList.add('no-run');
    const pending = err.message === 'HTTP 404';
    fill('run-card',
      h('div', { class: 'rc-title' }, pending ? '真实运行记录还在准备' : '运行记录读取失败'),
      h('p', { class: 'rc-task' }, pending
        ? '接下来会在 ARX R5 的左臂上，用语言模型插件重新跑一次“把木块放进盘子”，记下每一次发给模型的请求和模型的回复。整理好以后，每一步旁边会显示这次运行里对应的内容。现在页面上只有讲解部分。'
        : `（${err.message}）左边的讲解不受影响，可以刷新页面重试。`));
    const lede = $('lede-run');
    if (lede) lede.textContent = '这一页讲后一种情况：用语言模型控制机器人时，一次运行从头到尾依次发生什么。每一步先说发生了什么，再说为什么这样做，对应的源码放在每一步最后的折叠栏里。';
  });
})();
