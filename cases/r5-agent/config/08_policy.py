"""``claude`` 策略：Claude Code 会话本人当"语言模型"，通过文件信箱逐步下发动作。

没有外部模型 API：rollout / guardrail / 日志全走 inspect-robots 框架，只是 ``act()`` 不调 LLM，
而是把观测落盘、阻塞等信箱里出现下一条命令。命令语义参考 inspect-robots-agent 的 move_joints：
目标是绝对值、按每步限幅线性插值、单次播放不超过 10 s。与 agent 插件不同的一点：插值起点和未点名的维度用的是
**上一次下发的目标**而不是观测值——R5 的夹爪读数比指令低约 2 %、腕关节受重力有 ~0.05 rad 静差，若每次都从观测值
重新定目标，静差会被一步步"坐实"（棘轮漂移），而且首个路点会撞上核心的 delta 限幅（2026-09-19 真机实测）；``done`` / ``give_up``
走框架的 policy-stop 通道。附带一个桌面碰撞预检（FK 算末端和指尖高度），拒绝会撞桌的路点。

信箱目录（``-P mailbox=...``，默认 ``~/R5/logs/claude_driver``）：

* ``scene.json``            reset() 写：任务指令、维度名、控制频率、每步限幅、边界
* ``obs/NNNNNN.json``       每次 act() 写：env_step、关节状态、FK 末端/指尖、图片路径、审批/操作员消息
* ``obs/NNNNNN_<cam>.png``  该次观测的相机画面
* ``latest.json``           指向最新一条观测（seq、路径）
* ``cmd/NNNNNN.json``       Claude 写：``{"op": "move"|"hold"|"done"|"give_up", "targets": {...}, "note": "..."}``
* ``reply/NNNNNN.json``     策略写：命令处理结果（步数/秒数/目标，或错误；出错不发 chunk，继续等下一条）

序号 NNNNNN 由客户端递增；策略只处理比上一条大的命令，老文件不会被重复执行。
"""

from __future__ import annotations

import copy
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from inspect_robots.embodiment import EmbodimentInfo
from inspect_robots.policy import PolicyConfig, PolicyInfo
from inspect_robots.scene import Scene
from inspect_robots.spaces import ABSOLUTE_CONTROL_MODES, Box
from inspect_robots.types import Action, ActionChunk, Observation

_FALLBACK_HZ = 10.0
_MAX_DURATION_S = 10.0          # 与 agent 插件一致：单次 move 最多播放 10 s
_BACKSTOP_STEP_FRAC = 0.05      # 与核心 DeltaLimitApprover 默认一致：每步 ≤ 5 % 量程
_DEFAULT_SPEED_FRAC = 0.1       # agent 插件的默认 max_speed_frac；声明了 max_step 的维度按此比例缩放
_POLL_S = 0.1
_STOP_OPS = ("done", "give_up")

Vec = npt.NDArray[np.float64]


def _png_write(path: Path, rgb: np.ndarray) -> None:
    """写 PNG；优先 cv2（已随 arx_r5[cameras] 装好），退化到框架自带的 PNG 编码器。"""
    try:
        import cv2  # noqa: PLC0415

        cv2.imwrite(str(path), cv2.cvtColor(np.ascontiguousarray(rgb), cv2.COLOR_RGB2BGR))
        return
    except Exception:  # pragma: no cover - cv2 缺失或写盘失败时兜底
        pass
    from inspect_robots._pngenc import encode_png  # noqa: PLC0415

    path.write_bytes(encode_png(np.ascontiguousarray(rgb)))


class TableCheck:
    """R5 从臂桌面碰撞预检：用 ~/R5/r5_kin.py 的 FK 算 link6 原点和指尖高度。

    ``table_z`` 是桌面在 SDK 末端参考系里的高度（基座 link 原点即桌面：折叠位 link6 原点离桌 0.1635 m），
    ``tip_len`` 是 link6 原点到指尖沿工具 x 轴的距离（由 pick_place 实测的"z=0.05、俯仰 1.0 时指尖离桌约 10 cm"
    和数据集最低末端位置反推 ≈ 0.15 m）。只对以 ``<side>_j0..j5`` 命名的 6 关节块生效。
    """

    def __init__(
        self,
        kin_dir: str,
        *,
        table_z: float,
        tip_len: float,
        min_tip_clearance: float,
        min_ee_clearance: float,
        max_reach: float,
    ):
        if kin_dir not in sys.path:
            sys.path.insert(0, kin_dir)
        import r5_kin  # noqa: PLC0415

        self._kin = r5_kin
        self.table_z = table_z
        self.tip_len = tip_len
        self.min_tip = min_tip_clearance
        self.min_ee = min_ee_clearance
        self.max_reach = max_reach

    def pose(self, q6: Vec) -> dict[str, Any]:
        """返回末端 xyzrpy、指尖 xyz 和两者离桌高度。"""
        T = self._kin.fk_T(q6)
        p = T[:3, 3] - self._kin._P0
        tip = p + self.tip_len * T[:3, 0]
        rpy = self._kin.R2rpy(T[:3, :3])
        return {
            "ee_xyz": [round(float(v), 4) for v in p],
            "ee_rpy": [round(float(v), 3) for v in rpy],
            "tip_xyz": [round(float(v), 4) for v in tip],
            "ee_above_table": round(float(p[2] - self.table_z), 4),
            "tip_above_table": round(float(tip[2] - self.table_z), 4),
        }

    def reject(self, q6: Vec) -> str | None:
        """越界则返回原因，否则 None。"""
        info = self.pose(q6)
        if info["tip_above_table"] < self.min_tip:
            return f"fingertip would be {info['tip_above_table']*100:.1f} cm above the table (min {self.min_tip*100:.1f} cm)"
        if info["ee_above_table"] < self.min_ee:
            return f"wrist (link6) would be {info['ee_above_table']*100:.1f} cm above the table (min {self.min_ee*100:.1f} cm)"
        reach = math.hypot(info["tip_xyz"][0], info["tip_xyz"][1])
        if reach > self.max_reach:
            return f"fingertip reach {reach:.3f} m exceeds {self.max_reach:.2f} m"
        return None


class ClaudeDriverPolicy:
    """inspect-robots ``Policy``：观测落盘 → 等命令 → 插值成 chunk。"""

    accepts_operator_messages = True

    def __init__(
        self,
        *,
        mailbox: str = "~/R5/logs/claude_driver",
        max_speed_frac: float = 0.02,
        table_check: bool = True,
        kin_dir: str = "~/R5",
        table_z: float = -0.1635,
        tip_len: float = 0.15,
        min_tip_clearance: float = 0.008,
        min_ee_clearance: float = 0.06,
        max_reach: float = 0.50,
        pre_check=None,
    ):
        if not (math.isfinite(max_speed_frac) and 0 < max_speed_frac <= 1):
            raise ValueError(f"max_speed_frac must be in (0, 1], got {max_speed_frac!r}")
        self._root = Path(os.path.expanduser(mailbox))
        self._max_speed_frac = float(max_speed_frac)
        self._pre_check = pre_check
        self._table_cfg = None
        if table_check:
            self._table_cfg = dict(
                kin_dir=os.path.expanduser(kin_dir),
                table_z=table_z,
                tip_len=tip_len,
                min_tip_clearance=min_tip_clearance,
                min_ee_clearance=min_ee_clearance,
                max_reach=max_reach,
            )
        self._table: TableCheck | None = None
        self._labels: tuple[str, ...] = ()
        self._low = np.zeros(0)
        self._high = np.zeros(0)
        self._step_limits = np.zeros(0)
        self._hz: float | None = None
        self._state_key: str | None = None
        self._docs: str | None = None
        self._joint_blocks: list[tuple[str, int]] = []   # (side, start index) of 6-joint blocks
        self._seq = 0
        self._last_cmd = 0
        self._last_target: Vec | None = None    # 上一次下发的完整目标（插值起点 / 未点名维度的默认值）
        self._offset: Vec | None = None         # 观测 − 上次目标（稳态静差），预检时加到路点上
        self._hindsight: str | None = None
        self._transcript: list[dict[str, Any]] = []
        self._cursor = 0
        self._instruction: str | None = None
        self.info = PolicyInfo(name="claude", action_space=Box(shape=(1,), low=np.zeros(1), high=np.ones(1)))
        self.config = PolicyConfig(action_horizon=1, replan_interval=None)

    # -- framework hooks -------------------------------------------------------

    def bind(self, embodiment_info: EmbodimentInfo) -> None:
        """采用具身的动作空间；每步限幅算法与 agent 插件的 build_toolset 相同。"""
        box = embodiment_info.action_space
        low = np.asarray(box.low, dtype=np.float64).reshape(-1)
        high = np.asarray(box.high, dtype=np.float64).reshape(-1)
        dim = low.size
        sem = box.semantics
        if sem is None or sem.control_mode not in ABSOLUTE_CONTROL_MODES:
            raise ValueError("claude policy only drives absolute control modes (joint_pos / eef_abs_pose)")
        labels = tuple(sem.dim_labels) if sem.dim_labels else tuple(str(i) for i in range(dim))
        hz = embodiment_info.control_hz
        resolved = hz if hz is not None else _FALLBACK_HZ
        native_backstop = _BACKSTOP_STEP_FRAC * (high - low)
        step_frac = min(self._max_speed_frac / resolved, _BACKSTOP_STEP_FRAC)
        limits = np.minimum(step_frac * (high - low), native_backstop)
        declared = sem.max_step or (None,) * dim
        scale = min(self._max_speed_frac / _DEFAULT_SPEED_FRAC, 1.0)
        for i, entry in enumerate(declared):
            if entry is not None:
                limits[i] = min(float(entry) * scale, native_backstop[i])
        self._labels, self._low, self._high, self._step_limits, self._hz = labels, low, high, limits, hz
        spec = embodiment_info.observation_space.state
        self._state_key = None
        if spec is not None:
            for f in spec.fields:
                if f.shape == (dim,):
                    self._state_key = f.key
                    break
        self._docs = getattr(embodiment_info, "docs", None)
        self._joint_blocks = []
        for i, lab in enumerate(labels):
            if lab.endswith("_j0") and i + 6 < dim and labels[i + 6] == lab[:-3] + "_gripper":
                self._joint_blocks.append((lab[:-3], i))
        if self._table_cfg is not None and self._joint_blocks:
            self._table = TableCheck(**self._table_cfg)
        self.info = PolicyInfo(
            name="claude",
            action_space=box,
            observation_space=embodiment_info.observation_space,
            control_hz=hz,
        )

    def reset(self, scene: Scene) -> None:
        """清信箱、写 scene.json、重置 transcript。"""
        self._instruction = scene.instruction
        self._seq = 0
        self._last_cmd = 0
        self._last_target = None
        self._offset = None
        self._hindsight = None
        self._transcript = []
        self._cursor = 0
        for sub in ("obs", "cmd", "reply"):
            d = self._root / sub
            d.mkdir(parents=True, exist_ok=True)
            for f in d.iterdir():
                if f.is_file():
                    f.unlink()
        latest = self._root / "latest.json"
        if latest.exists():
            latest.unlink()
        self._write_json(
            self._root / "scene.json",
            {
                "instruction": scene.instruction,
                "labels": list(self._labels),
                "low": self._low.tolist(),
                "high": self._high.tolist(),
                "step_limits": self._step_limits.tolist(),
                "control_hz": self._hz,
                "max_speed_frac": self._max_speed_frac,
                "max_steps_per_call": self._max_steps_per_call(),
                "state_key": self._state_key,
                "docs": self._docs,
                "table_check": self._table_cfg,
                "started_at": time.time(),
            },
        )
        self._transcript.append({"role": "system", "content": f"goal: {scene.instruction}", "time": time.time()})

    def act(self, observation: Observation) -> ActionChunk:
        """落盘观测，然后阻塞直到信箱里出现一条能执行的命令。"""
        self._seq += 1
        current = self._current_state(observation)
        # 观测 − 上次目标 = 位置控制的稳态静差（R5 在伸展姿态下可达 0.06 rad）；桌面预检按"路点 + 静差"预测实际位姿
        self._offset = np.zeros_like(current) if self._last_target is None else np.clip(current - self._last_target, -0.1, 0.1)
        self._dump_observation(observation, current)
        while True:
            cmd_id, cmd = self._next_command()
            if cmd is None:
                time.sleep(_POLL_S)
                continue
            self._last_cmd = cmd_id
            args = {k: v for k, v in cmd.items() if k not in ("op", "note")}
            self._transcript.append(
                {
                    "role": "assistant",
                    "content": str(cmd.get("note") or ""),
                    "tool_calls": [{"id": f"cmd_{cmd_id}", "type": "function",
                                    "function": {"name": str(cmd.get("op")), "arguments": json.dumps(args, ensure_ascii=False)}}],
                    "time": time.time(),
                }
            )
            result = self._execute(cmd, current)
            reply = {k: v for k, v in result.items() if k != "chunk"}
            reply["cmd"] = cmd_id
            reply["obs_seq"] = self._seq
            self._write_json(self._root / "reply" / f"{cmd_id:06d}.json", reply)
            self._transcript.append(
                {"role": "tool", "tool_call_id": f"cmd_{cmd_id}", "content": self._reply_text(reply), "meta": reply, "time": time.time()}
            )
            chunk = result.get("chunk")
            if chunk is not None:
                return chunk

    def on_trial_end(self, record: Any, log_dir: str, run_id: str) -> None:
        """与 agent 插件对齐：把 done/give_up 带的 hindsight 写进 trial_metadata。"""
        if self._hindsight:
            try:
                record.metadata["hindsight"] = self._hindsight
            except Exception:  # metadata 不可写时不影响 trial 结果
                pass

    def transcript(self) -> list[dict[str, Any]]:
        """完整对话记录（深拷贝，不含图片字节）。"""
        return copy.deepcopy(self._transcript)

    def transcript_delta(self) -> list[dict[str, Any]] | None:
        """自上次调用以来新增的记录。"""
        new = self._transcript[self._cursor :]
        self._cursor = len(self._transcript)
        return copy.deepcopy(new) if new else None

    # -- internals --------------------------------------------------------------

    def _max_steps_per_call(self) -> int:
        resolved = self._hz if self._hz is not None else _FALLBACK_HZ
        return max(1, int(_MAX_DURATION_S * resolved))

    def _current_state(self, observation: Observation) -> Vec:
        if self._state_key is None or self._state_key not in observation.state:
            raise ValueError(f"observation has no state field matching the action space ({self._state_key!r})")
        cur = np.asarray(observation.state[self._state_key], dtype=np.float64).reshape(-1)
        if cur.size != self._low.size or not bool(np.all(np.isfinite(cur))):
            raise ValueError(f"bad proprioceptive state {cur.tolist()}")
        return cur

    def _kinematics(self, vec: Vec) -> dict[str, Any]:
        if self._table is None:
            return {}
        return {side: self._table.pose(vec[start : start + 6]) for side, start in self._joint_blocks}

    def _dump_observation(self, observation: Observation, current: Vec) -> None:
        stem = f"{self._seq:06d}"
        images: dict[str, str] = {}
        for name, img in observation.images.items():
            path = self._root / "obs" / f"{stem}_{name}.png"
            _png_write(path, np.asarray(img))
            images[name] = str(path)
        extra = observation.extra or {}
        rec = {
            "seq": self._seq,
            "env_step": extra.get("env_step"),
            "time": time.time(),
            "instruction": observation.instruction,
            "state": {lab: round(float(v), 4) for lab, v in zip(self._labels, current, strict=True)},
            "offset": None if self._last_target is None else
                      {lab: round(float(v), 4) for lab, v in zip(self._labels, self._offset, strict=True)},
            "commanded": None if self._last_target is None else
                         {lab: round(float(v), 4) for lab, v in zip(self._labels, self._last_target, strict=True)},
            "kinematics": self._kinematics(current),
            "images": images,
            "approvals": list(extra.get("approvals", [])),
            "operator_messages": list(extra.get("operator_messages", [])),
        }
        self._write_json(self._root / "obs" / f"{stem}.json", rec)
        self._write_json(self._root / "latest.json", {"seq": self._seq, "obs": str(self._root / "obs" / f"{stem}.json")})
        text = [f"observation {self._seq} (env_step {rec['env_step']}): "
                + " ".join(f"{k}={v:+.3f}" for k, v in rec["state"].items())]
        for side, kin in rec["kinematics"].items():
            text.append(f"{side} FK: EE xyz={kin['ee_xyz']} rpy={kin['ee_rpy']} tip xyz={kin['tip_xyz']} "
                        f"tip {kin['tip_above_table']*100:.1f} cm above table")
        if images:
            text.append("cameras: " + ", ".join(f"'{n}' (step {rec['env_step']})" for n in sorted(images)))
        if rec["approvals"]:
            text.append(f"guardrail interventions: {rec['approvals']}")
        for m in rec["operator_messages"]:
            text.append(f"operator (step {m.get('t')}): {m.get('text')}")
        self._transcript.append({"role": "user", "content": "\n".join(text), "meta": {"seq": self._seq, "images": images}, "time": rec["time"]})

    def _next_command(self) -> tuple[int, dict[str, Any] | None]:
        cmd_dir = self._root / "cmd"
        candidates = []
        for f in cmd_dir.glob("*.json"):
            try:
                n = int(f.stem)
            except ValueError:
                continue
            if n > self._last_cmd:
                candidates.append((n, f))
        if not candidates:
            return 0, None
        n, f = min(candidates)
        try:
            data = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError):
            return 0, None   # 还没写完，下一轮再读
        if not isinstance(data, dict):
            return n, {"op": "invalid", "note": "command file is not a JSON object"}
        return n, data

    def _execute(self, cmd: dict[str, Any], current: Vec) -> dict[str, Any]:
        op = str(cmd.get("op", ""))
        note = cmd.get("note")
        base = current.copy() if self._last_target is None else self._last_target.copy()
        base = np.clip(base, self._low, self._high)
        if op in _STOP_OPS:
            meta: dict[str, Any] = {"request_stop": True, "stop_reason": op, "stop_detail": str(note or "")}
            if cmd.get("hindsight"):
                meta["stop_hindsight"] = str(cmd["hindsight"])
                self._hindsight = str(cmd["hindsight"])
            action = Action(data=base, meta=meta)
            return {"ok": True, "op": op, "steps": 1, "chunk": ActionChunk(actions=[action], control_hz=self._hz)}
        if op == "hold":
            self._last_target = base
            action = Action(data=base, meta={"chunk_final": True})
            return {"ok": True, "op": op, "steps": 1, "seconds": 1 / self._hz if self._hz else None,
                    "chunk": ActionChunk(actions=[action], control_hz=self._hz)}
        if op != "move":
            return {"ok": False, "op": op, "error": f"unknown op {op!r}; use move / hold / done / give_up"}
        if not isinstance(note, str) or not note.strip():
            return {"ok": False, "op": op, "error": "note is required: what you see and why this motion"}
        targets = cmd.get("targets")
        if not isinstance(targets, dict) or not targets:
            return {"ok": False, "op": op, "error": "targets must be a non-empty object of label: value"}
        target = base.copy()
        named: list[int] = []
        index_by_label = {lab: i for i, lab in enumerate(self._labels)}
        for lab, raw in targets.items():
            i = index_by_label.get(str(lab))
            if i is None:
                return {"ok": False, "op": op, "error": f"unknown dimension {lab!r}; valid: {', '.join(self._labels)}"}
            try:
                v = float(raw)
            except (TypeError, ValueError):
                return {"ok": False, "op": op, "error": f"value for {lab!r} must be a number, got {raw!r}"}
            if not math.isfinite(v):
                return {"ok": False, "op": op, "error": f"value for {lab!r} must be finite"}
            if v < self._low[i] or v > self._high[i]:
                return {"ok": False, "op": op,
                        "error": f"target for {lab} is outside [{self._low[i]:.4g}, {self._high[i]:.4g}]"}
            target[i] = v
            named.append(i)
        # 可选的单次速度覆盖：speed_frac 相对 agent 的 max_speed_frac 语义，封顶到 0.1（即声明的 max_step 原值）
        limits = self._step_limits
        speed = cmd.get("speed_frac")
        if speed is not None:
            try:
                speed = float(speed)
            except (TypeError, ValueError):
                return {"ok": False, "op": op, "error": "speed_frac must be a number"}
            if not (math.isfinite(speed) and 0 < speed <= _DEFAULT_SPEED_FRAC):
                return {"ok": False, "op": op, "error": f"speed_frac must be in (0, {_DEFAULT_SPEED_FRAC}]"}
            limits = self._step_limits * (speed / self._max_speed_frac)
            limits = np.minimum(limits, _BACKSTOP_STEP_FRAC * (self._high - self._low))
        ratio = 0.0
        for i in named:
            d = abs(target[i] - base[i])
            if d > 0 and limits[i] > 0:
                ratio = max(ratio, d / limits[i])
        steps = max(1, math.ceil(ratio / (1.0 - 1e-6)))
        if steps > self._max_steps_per_call():
            return {"ok": False, "op": op,
                    "error": f"motion needs {steps} steps, exceeds the {_MAX_DURATION_S:g}s playout cap; split it"}
        fractions = np.linspace(1.0 / steps, 1.0, steps)
        waypoints = [np.clip(base + (target - base) * f, self._low, self._high) for f in fractions]
        waypoints[-1] = np.clip(target.copy(), self._low, self._high)
        # An embodiment can mirror its existing actual-send constraints here.
        # Reject the entire move BEFORE emitting a chunk or changing _last_target.
        if self._pre_check is not None:
            reason = self._pre_check(np.asarray(waypoints))
            if reason:
                return {"ok": False, "op": op, "error": "command pre-check rejected motion: " + reason}
        if self._table is not None:
            offset = self._offset if self._offset is not None else np.zeros_like(target)
            for k, wp in enumerate(waypoints):
                predicted = wp + offset
                for side, start in self._joint_blocks:
                    reason = self._table.reject(predicted[start : start + 6])
                    if reason:
                        return {"ok": False, "op": op,
                                "error": f"table check rejected waypoint {k + 1}/{steps} ({side}, predicted actual pose incl. static offset): {reason}"}
        actions = [Action(data=wp) for wp in waypoints]
        actions[-1] = Action(data=waypoints[-1], meta={"chunk_final": True})
        self._last_target = waypoints[-1].copy()
        seconds = steps / self._hz if self._hz else None
        return {
            "ok": True,
            "op": op,
            "steps": steps,
            "start": {self._labels[i]: round(float(base[i]), 4) for i in named},
            "seconds": None if seconds is None else round(seconds, 2),
            "target": {self._labels[i]: round(float(target[i]), 4) for i in named},
            "target_kinematics": self._kinematics(waypoints[-1]),
            "predicted_kinematics": self._kinematics(waypoints[-1] + (self._offset if self._offset is not None else 0)),
            "chunk": ActionChunk(actions=actions, control_hz=self._hz),
        }

    @staticmethod
    def _reply_text(reply: dict[str, Any]) -> str:
        if not reply.get("ok"):
            return f"error: {reply.get('error')}"
        text = f"{reply.get('op')}: accepted, {reply.get('steps')} steps"
        if reply.get("seconds") is not None:
            text += f" ({reply['seconds']} s)"
        if reply.get("target"):
            text += "; target " + " ".join(f"{k}={v:+.3f}" for k, v in reply["target"].items())
        tk = reply.get("target_kinematics") or {}
        for side, kin in tk.items():
            text += f"; {side} target EE xyz={kin['ee_xyz']} pitch={kin['ee_rpy'][1]} tip {kin['tip_above_table']*100:.1f} cm above table"
        return text

    @staticmethod
    def _write_json(path: Path, data: Any) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1))
        os.replace(tmp, path)


def claude_policy(**kwargs: Any) -> ClaudeDriverPolicy:
    """注册入口：``inspect-robots ... --policy claude -P mailbox=... -P max_speed_frac=...``。"""
    return ClaudeDriverPolicy(**kwargs)
