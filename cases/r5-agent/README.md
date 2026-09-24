# Claude Opus 5.5 API 真机记录

本例使用官方 Anthropic Messages API、`claude-opus-5-5`、medium、标准速度。
任务仅为桌面 cube 入蓝盘；随后显式回零属于 RESTORE，不包含盘内取回的 B 阶段。
单臂 can1/type0，单一进程持有 CAN 和两路相机。代码与操作说明冻结在 `config/`，版本见 `versions.json`。

## 实际结果

- 入盘独立图像复核：True。
- 入盘现场耗时：882.088257597 秒；回零：238.4783403 秒。
- 抓取尝试：1。
- 复核详情：`visual_review.json`，不得用框架 status=success 代替物理成功。
- 每阶段统计及费用：`result.json`、`phases.csv`；每请求：`api_requests.csv`。
- API 入盘成功；回零途中出现一次请求上传超时（重试成功），随后 HTTP 413 请求体过大。模型全程保留图片历史，至此累积的请求超过接口限制。未在实验中改动方案；Codex 按用户指示经同一 SDK 显式完成最后折叠与闭合空爪，记录在 ADMIN_RECOVERY / admin_cmd / admin_reply 中。这次不是“入盘加回零全部由 API 无干预完成”。

## 全链路文件对应

1. `run.json`：网页逐回合摘要、实际请求参数、完整工具 schema 和最初说明。
2. `wire.json`：逐次 HTTP 请求与响应，保留失败／重试、API message id、usage；图片通过 img/ 引用。
3. `eval-log.json`：原生 EvalLog、原生策略对话与 action / wire sidecar 指针。
4. `actions.jsonl`：原生框架交付的逐步 Action。只观察／停止也是框架 step，不能全算机械运动。
5. `events.jsonl`：单调时钟及 UTC；goal handoff、逐工具请求与结果、真实 SDK target 与 joint readback、观测、验握／放置、阶段边界。
6. `observations/`：每次模型观察的两路原始 PNG、真实关节、commanded 关节、两种 FK。序号可对应 events 的 obs_seq。
7. `video/`：框架采样帧回放；播放时长不能代替包含 API 等待的现场耗时。
8. `wire/`、`actions/`、`transcripts/`：未重新编排的原生 sidecar；wire 中的 `$blob` 可用同目录树的 PNG 重建原始请求图片。逐控制步的原始 NPY 帧留在本机，网页提供回放和模型实际观察到的全部原图。

API key 和 HTTP 鉴权头不进入这些文件。

## 接入方式与边界

原生 `LLMAgentPolicy` → `AnthropicClient` → `Toolset` → inspect-robots eval / approver → R5 SDK。
本机包装器保留原生 agent、Messages 转换、thinking signature 原样回传、WireCapture 和动作记录。
R5 专用扩展只提供已验证的 IK、像素平面测量、只读观察和显式视觉检查点；动作仍由 API 模型根据本轮图像逐步决定。
插值锚点使用上一次实际下发的 command，模型所见关节始终为真实读回；两者都保存。
`config/00_inspect_opus_run.py` 记录这层扩展。它不是未经修改的默认命令行示例，网页左侧源码讲解与本轮框架版本以各自 commit 为准。

## 统计口径

真实模型请求数按 HTTP 请求及 wire logical call / attempt 归属统计，不用观察数或工具数代替。
Anthropic input_tokens 是未缓存输入；完整输入 = 未缓存 + cache creation + cache read。
thinking_tokens 已包含在 output_tokens 内；不重复相加，缺失字段保留 null。
成本按 API 返回用量及官方公开单价估算，不是账单。
单价来源：https://platform.claude.com/docs/en/about-claude/pricing（2026-09-24 查验）。
软件准备的 API 调用单独保存在本地 preparation 目录；Codex 开发和报告整理不计入本次 Claude 控制 API 用量。
这是一次 API 真机案例，不将单次结果外推为模型总体成功率。
