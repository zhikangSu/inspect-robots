/* Browse recorded data only. There is no hardware or command endpoint. */
(() => {
  'use strict';
  const base = 'cases/r5-cube/';
  const $ = id => document.getElementById(id);
  const text = (id, value) => { $(id).textContent = value; };
  const pad = n => String(n).padStart(2, '0');
  const clock = seconds => `${pad(Math.floor(seconds / 60))}:${pad(Math.floor(seconds % 60))}`;
  const video = $('r5-video');
  const dialog = $('r5-image-dialog');
  let data, selected = 1, camera = 'fixed', switching = false, skipCameraSeek = false;

  function parseHash() {
    const m = /^#r5-obs-(\d+)$/.exec(location.hash);
    return m ? Math.min(41, Math.max(1, Number(m[1]))) : null;
  }

  function openImage(path, title) {
    $('r5-dialog-image').src = base + path;
    $('r5-dialog-image').alt = title;
    $('r5-dialog-download').href = base + path;
    text('r5-dialog-title', title);
    dialog.showModal();
  }

  function select(seq, {history = true, fromVideo = false} = {}) {
    if (!data) return;
    if (!fromVideo) video.pause();
    selected = Math.max(1, Math.min(data.observations.length, seq));
    const o = data.observations[selected - 1];
    const next = data.observations[selected] || null;
    const c = o.next_command, reply = o.reply;
    text('r5-observation-title', o.title);
    text('r5-counter', `OBS ${pad(selected)} / ${data.observations.length}`);
    text('r5-observation-meta', `首次策略观测后 ${clock(o.elapsed_s)} · 已执行 ${o.env_step.toLocaleString()} 个小步 · 回放 ${o.replay_s.toFixed(2)} s`);
    $('r5-range').value = selected;
    $('r5-range').setAttribute('aria-valuetext', `第 ${selected} 组，共 41 组：${o.title}`);
    $('r5-prev').disabled = selected === 1;
    $('r5-next').disabled = selected === data.observations.length;
    for (const cam of ['fixed', 'wrist']) {
      const title = `观察 ${selected} · ${cam === 'fixed' ? '固定' : '腕部'}相机 · ${o.title}`;
      $(`r5-${cam}-image`).src = base + o.images[cam];
      $(`r5-${cam}-image`).alt = title;
      $(`r5-${cam}-open`).onclick = () => openImage(o.images[cam], title);
    }
    text('r5-explanation', o.explanation);
    const kind = c.op === 'done' ? '结束 · done' : c.op === 'hold' ? '保持并更新观测' :
      Object.keys(c.targets).every(k => k.endsWith('_gripper')) ? '改变夹爪开度' : '移动到关节目标';
    text('r5-command-kind', kind);
    text('r5-chunk', c.op === 'done' ? '终止记录，不执行运动' : `${reply.steps} 小步 · ${reply.seconds.toFixed(2)} s`);
    const residual = o.last_commanded ? Math.max(...Object.keys(o.state)
      .filter(k => !k.endsWith('_gripper')).map(k => Math.abs(o.state[k] - o.last_commanded[k]))) : null;
    text('r5-residual', residual === null ? '初始观测，无上一条目标' : `${residual.toFixed(3)} rad（最大值）`);
    text('r5-after-summary', next ? `这条指令之后是观察 ${next.seq}：${next.title}。折算秒数来自配置的 20 Hz，不含策略等待，也不保证现场每步正好 50 ms。` :
      '这是末次策略观测。随后 done 请求结束，控制进程正常退出；页面下方还有 SDK 释放后另拍的最终照片。');
    $('r5-after').disabled = !next;
    text('r5-after', next ? `看执行后的观察 ${next.seq} →` : '已到末次策略观测');
    text('r5-command-record', `command #${c.id}\n${c.note}\n\n` +
      (c.targets ? JSON.stringify(c.targets, null, 2) : c.op === 'hold' ? '保持上一条完整目标，获取新的观测。' : 'done：请求终止，不发送新的运动目标。'));
    const rows = $('r5-state-rows');
    rows.replaceChildren();
    for (const key of Object.keys(o.state)) {
      const row = document.createElement('tr');
      const label = key.endsWith('_gripper') ? '夹爪（0–1）' : `J${Number(key.slice(-1)) + 1}（rad）`;
      const target = c.targets?.[key];
      const values = [label, o.state[key].toFixed(4), target === undefined ? (c.op === 'done' ? '—' : '保持') : target.toFixed(4), next ? next.state[key].toFixed(4) : '—'];
      for (const value of values) {
        const cell = document.createElement('td'); cell.textContent = value; row.append(cell);
      }
      rows.append(row);
    }
    $('r5-depth').hidden = !o.depth;
    if (o.depth) {
      $('r5-depth-image').src = base + o.depth.image;
      text('r5-depth-info', `有效深度像素 ${(o.depth.valid_fraction * 100).toFixed(1)}%。显示范围统一为 0.05–0.35 m，超出范围的有效值显示端点颜色。`);
      $('r5-depth-open').onclick = () => openImage(o.depth.image, `观察 ${selected} · 额外深度核验（5–35 cm 伪彩色）`);
    } else {
      $('r5-depth').open = false;
    }
    document.querySelectorAll('[data-r5-seq]').forEach(b => b.setAttribute('aria-current', String(Number(b.dataset.r5Seq) === selected)));
    document.querySelectorAll('[data-r5-stage]').forEach(b => {
      if (b.dataset.r5Stage === o.stage) b.setAttribute('aria-current', 'step');
      else b.removeAttribute('aria-current');
    });
    if (history) window.history.replaceState(null, '', `#r5-obs-${selected}`);
  }

  function seek(seconds) {
    const apply = () => { video.currentTime = Math.min(seconds, Number.isFinite(video.duration) ? video.duration - .05 : seconds); };
    if (video.readyState >= 1) apply();
    else video.addEventListener('loadedmetadata', apply, {once: true});
  }

  function switchCamera(cam) {
    if (cam === camera || switching) return;
    const t = video.currentTime, wasPlaying = !video.paused, rate = Number($('r5-speed').value);
    switching = true; camera = cam;
    video.pause();
    video.src = base + `${cam}.mp4`;
    video.poster = base + `images/01-${cam}.webp`;
    video.setAttribute('aria-label', `R5 木块入盘动作回放，${cam === 'fixed' ? '固定' : '腕部'}相机`);
    document.querySelectorAll('[data-r5-camera]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.r5Camera === cam)));
    video.addEventListener('loadedmetadata', () => {
      if (t > 0) {
        skipCameraSeek = true;
        video.currentTime = Math.min(t, video.duration - .05);
      }
      video.playbackRate = rate;
      switching = false;
      if (wasPlaying) video.play().catch(() => {});
    }, {once: true});
    video.load();
  }

  $('r5-dialog-close').onclick = () => dialog.close();
  dialog.addEventListener('click', event => {
    const rect = dialog.getBoundingClientRect();
    if (event.target === dialog && (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom)) dialog.close();
  });
  document.querySelectorAll('[data-r5-camera]').forEach(b => { b.onclick = () => switchCamera(b.dataset.r5Camera); });
  $('r5-speed').onchange = () => { video.playbackRate = Number($('r5-speed').value); };
  $('r5-prev').onclick = () => select(selected - 1);
  $('r5-next').onclick = $('r5-after').onclick = () => select(selected + 1);
  $('r5-range').oninput = event => select(Number(event.target.value));
  $('r5-seek').onclick = () => {
    seek(data.observations[selected - 1].replay_s);
    $('r5-replay').scrollIntoView({block: 'start'});
  };
  function syncObservation() {
    if (!data || switching || !$('r5-follow').checked) return;
    const match = data.observations.findLast(o => o.replay_s <= video.currentTime + .001);
    if (match && match.seq !== selected) select(match.seq, {history: false, fromVideo: true});
  }
  video.addEventListener('timeupdate', () => {
    if (!video.paused) syncObservation();
  });
  video.addEventListener('seeked', () => {
    if (skipCameraSeek) skipCameraSeek = false;
    else syncObservation();
  });
  video.addEventListener('error', () => { switching = false; });
  const hashChanged = () => {
    const seq = parseHash();
    if (seq && data) {
      select(seq, {history: false});
      $('r5-observations').scrollIntoView({block: 'start'});
    }
  };
  window.addEventListener('hashchange', hashChanged);

  fetch(base + 'data.json').then(response => {
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }).then(payload => {
    if (payload.observations?.length !== 41 || payload.summary.camera_image_count !== 82) throw new Error('观测数据不完整');
    data = payload;
    for (const stage of data.stages) {
      const b = document.createElement('button');
      b.type = 'button'; b.dataset.r5Stage = stage.id;
      b.textContent = `${stage.label} ${pad(stage.first)}–${pad(stage.last)}`;
      b.onclick = () => select(stage.first);
      $('r5-stages').append(b);
    }
    for (const o of data.observations) {
      const b = document.createElement('button');
      b.type = 'button'; b.className = 'r5-thumb'; b.dataset.r5Seq = o.seq;
      b.setAttribute('aria-label', `选择观察 ${o.seq}：${o.title}`);
      const im = document.createElement('img');
      im.src = base + o.thumbnail; im.alt = ''; im.loading = 'lazy'; im.width = 160; im.height = 120;
      const label = document.createElement('span'); label.textContent = `${pad(o.seq)} · ${clock(o.elapsed_s)}`;
      b.append(im, label); b.onclick = () => {
        select(o.seq);
        document.querySelector('.r5-observer').scrollIntoView({block: 'start'});
      };
      $('r5-thumbnails').append(b);
    }
    $('r5-load-status').hidden = true;
    $('r5-browser').hidden = false;
    select(parseHash() || 1, {history: false});
    if (parseHash()) hashChanged();
  }).catch(error => {
    $('r5-load-status').className = 'r5-error';
    text('r5-load-status', `观测索引加载失败（${error.message}）。请刷新页面；下方的视频、最终照片和数据下载仍可使用。`);
  });
})();
