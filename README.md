# Inspect Robots 控制回路解剖（网页源码）

这个分支是 GitHub Pages 站点 https://zhikangsu.github.io/inspect-robots/ 的全部内容。

## 文件

| 文件 | 作用 |
|---|---|
| `index.html` | 唯一的源文件，也是发布出去的页面。所有文字、样式、图都在这一个文件里。 |
| `tools/embed_snippets.py` | 把页面里源码链接指向的代码片段抽出来嵌进 `index.html` 底部，并加上右侧抽屉。 |
| `.nojekyll` | 让 GitHub Pages 原样发布，不走 Jekyll。 |

## 怎么改

改文字、表格、图：直接编辑 `index.html`，提交推送即可，几十秒后生效。

页面的结构：

- `<style>` 里是全部样式。颜色用 CSS 变量，`--model`（蓝，模型侧）、`--safety`（橙，安全检查）、`--robot`（青绿，机器人侧），亮暗两套主题都在里面。
- `<nav class="toc">` 是左侧目录，`<main>` 是正文。正文按三个阶段（`#phase1` `#phase2` `#phase3`）组织，每一步是一个 `<article class="step" id="sN">`，里面依次是标题、"发生什么"段落、`div.why`（为什么这样设计）、`details`（对应代码）。
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

脚本是幂等的：它先删掉旧的抽屉样式、JSON 和脚本，再按当前页面里的链接重新生成。只给了起始行的链接，它会自动找到该函数或类的结尾（最多 170 行）；给了 `#L起始-L结束` 就按给定范围抽取。指向目录的链接（`/tree/`）不会嵌片段。

## 不要动的部分

- 页面底部 `<!-- source drawer -->` 之后的内容全部由脚本生成，手改会在下次运行时被覆盖。
- `<style>` 里 `/* ---- source drawer ---- */` 到 `</style>` 之间同理。
- 把链接换到别的提交时，要同时改脚本里的 `SHA`，并重新核对行号。
