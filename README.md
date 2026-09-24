# Inspect Robots：语言模型怎样控制机器人（网页源码）

这个分支是 GitHub Pages 站点 https://zhikangsu.github.io/inspect-robots/ 的全部内容。

页面按一次运行的先后顺序讲 12 步。每一步左边是讲解，右边是一次真实运行里对应的记录：发给模型的原始请求、模型的回复和工具调用、拆成的小步、执行结果。

## 文件

| 文件 | 作用 |
|---|---|
| `index.html` | 页面正文和样式，也是发布出去的首页。左边的讲解都写在这里。 |
| `assets/run-panel.js` / `assets/run-panel.css` | 右边各步的内容：读取运行记录，按步骤和回合显示；还有完整请求查看器和 token 用量图。只读数据，不连接机器人。 |
| `cases/r5-agent/` | 右边用的那次运行（ARX R5 左臂，语言模型插件），由下面的导出工具生成。 |
| `tools/export_agent_run.py` | 把一次 `--policy agent` 运行的日志目录导出成 `cases/<名字>/`。 |
| `tools/embed_snippets.py` | 把页面里源码链接指向的代码片段抽出来嵌进 `index.html` 底部，并加上源码抽屉。 |
| `annotations/source.zh-CN.json` | 68 个源码/设计文档片段的中文解读，按文件与行号范围维护；含原文哈希和注释锚点。 |
| `assets/source-drawer.css` / `assets/source-drawer.js` | 源码抽屉的注释/原文切换、语法着色与键盘交互；由生成工具嵌入首页。 |
| `tools/preview.py` | 本地预览服务器，支持视频拖动所需的 HTTP Range。 |
| `cases/r5-cube/`、`tools/export_r5_case.py` | 旧的一次 Codex 运行记录（通过文件做决定，没有模型 API 请求）。页面不再展示，只作存档。 |
| `.nojekyll` | 让 GitHub Pages 原样发布，不走 Jekyll。 |

## 页面结构

- `<nav class="toc">` 是左侧目录，`<main>` 是正文。`<div class="page" data-run="cases/r5-agent/">` 的 `data-run` 决定右边读哪次运行。
- 每一步是一个 `<article class="step" id="sN">`：`div.explain` 是左边的讲解（标题、正文、`div.why`、折叠的“对应代码”），`aside.ev` 是右边，里面的 `div#ev-sN` 由 `run-panel.js` 填充。
- 第 6–9 步放在 `.turn-scope` 里，上方的回合选择条固定在顶部，切换回合时这四步的右边一起更新。`#turn-5` 这样的链接可以直接打开某个回合。
- “出问题时怎么办”的每张卡片有 `data-branch`，脚本会在卡片最后写上这次运行里有没有出现过、出现在哪几个回合。
- 颜色用 CSS 变量：`--model`（蓝，语言模型和模型插件）、`--safety`（橙，安全检查）、`--robot`（青绿，机器人插件），灰色是框架。token 图用 `--series-1/2/3`（已用配色检查脚本验证过亮暗两种背景）。

## 换一次运行

1. 用语言模型插件跑一次，保持默认的请求记录（`wire_capture`）打开，建议加 `--store-frames`：

   ```bash
   inspect-robots "..." --policy agent -P model=anthropic/claude-sonnet-5 -P wire=messages --store-frames --log-dir LOG_DIR
   ```

2. 如果要在页面里放回放视频，在有 ffmpeg 的机器上生成：

   ```bash
   inspect-robots video LOG_DIR/<日志文件>.json --out LOG_DIR/video
   ```

3. 导出（需要 macOS 的 `sips` 把图片转成 JPEG；没有时会直接复制 PNG）：

   ```bash
   python3 tools/export_agent_run.py LOG_DIR cases/<名字> --video LOG_DIR/video --rig rig.json
   ```

   `rig.json` 可选，写日志里没有记录的机器人设置，例如 `{"joint_max_step": 0.1, "gripper_max_step": 0.2}`，用于第 4 步显示每小步的上限。

4. 把 `index.html` 里的 `data-run` 改成 `cases/<名字>/`。不改的话，也可以用 `?run=cases/<名字>/` 临时预览。

导出结果：`run.json` 是右边读取的整理结果；`wire.json` 是每一次请求和回复的原文（图片换成了 `img/` 里的文件）；`eval-log.json` 是去掉本机路径后的主日志；`actions.jsonl` 是每一小步发出的数值。请求记录只有请求体，没有请求头，所以不含 API key。

## 用词约定

页面面向第一次接触这个项目的人，正文用平常的说法，代码名只放在折叠的“对应代码”和最后的对照表里。

- 五个部分固定这样叫：语言模型、模型插件、框架、安全检查、机器人插件。不要再用“模型侧”泛指模型插件的代码。
- 常用词：观测（关节位置加相机画面）、回合（问一次模型并走完它的动作）、小步（拆分后的每一步）、操作（模型能用的移动、拍照、完成、放弃）。
- 不用比喻，不写“X 是兜底，不是常态”这类总结句，也不写“不能把 A 当作 B”“这不表示……”这类防御式声明。需要说明的限制，直接写事实，一句就够。
- 右边保留英文原文，旁边配中文。固定英文句子（系统提示、工具描述、框架回复）的中文写在 `assets/run-panel.js` 的 `ZH` 表里，按整句匹配；模型自己写的内容不翻译。

## 新增或修改源码链接后

需要重新生成嵌入的代码片段：

```bash
git clone https://github.com/zhikangSu/inspect-robots ../inspect-robots-src   # 任何含提交 7e4d1b7 的 clone 都行
INSPECT_ROBOTS_REPO=../inspect-robots-src python3 tools/embed_snippets.py index.html
```

脚本是幂等的：它先删掉旧的抽屉样式、JSON 和脚本，再按当前页面里的链接重新生成，并重建 `data-snip` 映射。只给了起始行的链接，它会自动找到代码块的结尾（最多 170 行）；给了 `#L起始-L结束` 就按给定范围抽取。

每个源码链接都要在 `annotations/source.zh-CN.json` 里有对应的中文注释（用途、输入、产出、例子与分段注释）。生成器检查源码 SHA-256、注释行号范围和对应原文锚点；缺注释、固定版本改变或锚点错位时会停止。

## 不要动的部分

- 页面底部 `<!-- source drawer -->` 之后的内容全部由脚本生成，手改会在下次运行时被覆盖。
- `<style>` 里 `/* ---- source drawer ---- */` 到 `</style>` 之间同理。
- 把源码链接换到别的提交时，要同时改脚本与注释文件里的提交号，并逐段重审原文、哈希、行号和中文解释。

## 本地预览

```bash
python3 tools/preview.py --port 8765
```

打开 `http://127.0.0.1:8765/`。普通 `python -m http.server` 也能看页面，但 Chrome 里可能无法拖动视频。
