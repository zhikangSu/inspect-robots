# Inspect Robots 控制回路解剖（网页源码）

这个分支是 GitHub Pages 站点 https://zhikangsu.github.io/inspect-robots/ 的全部内容。

## 文件

| 文件 | 作用 |
|---|---|
| `index.html` | 页面正文、原有图解与样式，也是发布出去的首页。R5 案例的说明和入口也在这里。 |
| `assets/r5-case.css` / `assets/r5-case.js` | R5 实例的响应式浏览器、原图放大、回放定位与观测联动；不连接机器人。 |
| `cases/r5-cube/` | 2026-09-19 实机案例：41 组双路原图、7 张深度可视化、两路视频、数据与来源校验值。 |
| `tools/export_r5_case.py` | 从这次本地运行记录导出网页案例；只发布明确选定的字段，不复制本机路径。 |
| `tools/embed_snippets.py` | 把页面里源码链接指向的代码片段抽出来嵌进 `index.html` 底部，并加上右侧抽屉。 |
| `annotations/source.zh-CN.json` | 68 个源码/设计文档片段的中文解读，按文件与行号范围维护；含原文哈希和注释锚点。 |
| `assets/source-drawer.css` / `assets/source-drawer.js` | 源码抽屉的注释/原文切换、语法着色与键盘交互；由生成工具嵌入首页。 |
| `.nojekyll` | 让 GitHub Pages 原样发布，不走 Jekyll。 |

## 怎么改

改文字、表格、图：直接编辑 `index.html`，提交推送即可，几十秒后生效。

页面的结构：

- `<style>` 里是全部样式。颜色用 CSS 变量，`--model`（蓝，模型侧）、`--safety`（橙，安全检查）、`--robot`（青绿，机器人侧），亮暗两套主题都在里面。
- `<nav class="toc">` 是左侧目录，`<main>` 是正文。正文按三个阶段（`#phase1` `#phase2` `#phase3`）组织，每一步是一个 `<article class="step" id="sN">`，里面依次是标题、"发生什么"段落、`div.why`（为什么这样设计）、`details`（对应代码）。
- `#r5-case` 把 R5 实机记录对应到前文 12 步；`#r5-obs-33` 这类链接可以直达一组观测。浏览器只读取静态 JSON 和媒体文件。
- 每个源码链接长这样：

  ```html
  <a class="src" href="https://github.com/robocurve/inspect-robots/blob/7e4d1b7aee1c0d3cfc3a05a7492b9d12cda666f9/src/inspect_robots/rollout.py#L325">rollout.py:325 主循环开始</a>
  ```

  `href` 必须是固定到提交 `7e4d1b7` 的 blob 地址，带 `#L起始` 或 `#L起始-L结束`。运行下面的脚本后它会自动获得 `data-snip="sN"`，点击时右侧抽屉显示对应代码。

## 新增或修改源码链接后

需要重新生成嵌入的代码片段：

```bash
git clone https://github.com/zhikangSu/inspect-robots ../inspect-robots-src   # 任何含提交 7e4d1b7 的 clone 都行
INSPECT_ROBOTS_REPO=../inspect-robots-src python3 tools/embed_snippets.py index.html
```

脚本是幂等的：它先删掉旧的抽屉样式、JSON 和脚本，再按当前页面里的链接重新生成，并重建 `data-snip` 映射。只给了起始行的链接，它会自动找到代码块的结尾（最多 170 行）；给了 `#L起始-L结束` 就按给定范围抽取。Markdown 标题锚点会定位对应章节，目录链接不会嵌片段。

抽屉默认显示“中文注释”：先解释片段负责什么、输入和产出，再在原代码块之间插入中文说明。注释行用 `#` 和“中文解读”标识，不占用 GitHub 原始行号；可切换“原始代码”核对。

修改中文说明时编辑 `annotations/source.zh-CN.json`，然后重新生成首页。每个片段必须包含用途、输入、产出、例子与分段注释。生成器检查源码 SHA-256、注释行号范围和对应原文锚点；新增链接缺少注释、固定版本改变或锚点错位时会停止，而不是发布失配的解释。阅读注释不修改上游源码，也不进入实际机器人控制路径。

## 不要动的部分

- 页面底部 `<!-- source drawer -->` 之后的内容全部由脚本生成，手改会在下次运行时被覆盖。
- `<style>` 里 `/* ---- source drawer ---- */` 到 `</style>` 之间同理。
- 把链接换到别的提交时，要同时改脚本与注释文件里的提交号，并逐段重审原文、哈希、行号和中文解释。不能只更新哈希跳过语义复核。

## R5 实例的图像与数据

`cases/r5-cube/annotations.json` 是按原始动作记录整理的中文阅读提示。
`data.json` 保存每次进入策略的读回状态、下一条指令、切分结果、图像索引与时间。
这两个时点不可交换：观察 N 对应指令 N 执行之前，执行结果应看观察 N+1。

相机图以无损 WebP 保存，导出器逐张比较解码后的 RGB 像素与源 PNG。
`thumbs/` 是单独缩小的导航图，`images/` 保留 640×480 原尺寸。
深度图是 5–35 cm 固定色标的派生可视化；额外深度核验只发生在 7 次观察附近。
视频按保存帧 20 fps 合成，77.75 秒不等于约 13 分 52 秒的现场运行时间。

本次使用文件信箱策略（注册名沿用 `claude`，由当前 Codex 会话决策），并非独立模型 API 调用。
物理成功来自两路图像与撤离后照片；原评测只启用了 `episode_length`，没有自动成功评分。
因此不能把 41 组观测写成 API 调用次数，也不能把框架 `status=success` 当作实物成功证据。

重新导出这一次案例（需要 Pillow、numpy）：

```bash
python tools/export_r5_case.py /path/to/codex_cube_20260919_run3
python tools/preview.py --port 8765
```

打开 `http://127.0.0.1:8765/#r5-case`。应检查全部 41 组图片、上一组/下一组、
阶段跳转、末组禁用、深度折叠区、原图弹窗、视频时间定位与相机切换、手机布局，
同时确认原源码抽屉仍可用。导出器针对这次已核验的 episode；新增实验应建立独立案例及对应说明。

本地预览使用 tools/preview.py，它支持浏览器定位 MP4 所需的 HTTP Range。普通 python -m http.server 可以看页面和图片，但可能无法在 Chrome 中跳转视频时间。
