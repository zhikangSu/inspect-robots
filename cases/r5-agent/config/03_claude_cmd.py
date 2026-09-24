#!/usr/bin/env python3
"""inspect-robots ``claude`` 策略的命令行客户端：Claude 在会话里用它逐步驱动臂。

用法（信箱目录默认 ~/R5/logs/claude_driver，与策略的 -P mailbox 一致）：
  python3 claude_cmd.py state                                  # 最新观测：关节、FK 末端/指尖、图片路径
  python3 claude_cmd.py move left_j1=1.2 left_j2=1.2 --note "…" [--speed 0.05]
  python3 claude_cmd.py ee X Y Z --pitch P [--roll R] [--yaw Y] --note "…"   # IK（r5_kin）→ 六关节目标
  python3 claude_cmd.py rel DX DY DZ [--dpitch DP] --note "…"  # 相对当前末端位置
  python3 claude_cmd.py grip 0~1 --note "…"                    # 0 闭合 1 张开
  python3 claude_cmd.py hold                                   # 不动，只要一帧新观测
  python3 claude_cmd.py done --note "…" / giveup --note "…"
每条会动的命令都等策略回复，再等下一帧观测到达后打印；出错（越界 / 预检拒绝）直接打印错误，不发 chunk。
"""

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import r5_kin as K  # noqa: E402

DEFAULT_MAILBOX = Path(os.path.expanduser("~/R5/logs/claude_driver"))


def read_json(path: Path, retries: int = 20):
    for _ in range(retries):
        try:
            return json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            time.sleep(0.05)
    raise SystemExit(f"cannot read {path}")


def latest(root: Path, timeout: float = 30.0):
    p = root / "latest.json"
    t0 = time.time()
    while not p.exists():
        if time.time() - t0 > timeout:
            raise SystemExit(f"no observation yet in {root} (is the eval running?)")
        time.sleep(0.2)
    meta = read_json(p)
    return meta["seq"], read_json(Path(meta["obs"]))


def wait_obs_after(root: Path, seq: int, timeout: float):
    t0 = time.time()
    while True:
        p = root / "latest.json"
        if p.exists():
            meta = read_json(p)
            if meta["seq"] > seq:
                return meta["seq"], read_json(Path(meta["obs"]))
        if time.time() - t0 > timeout:
            raise SystemExit(f"timed out after {timeout:.0f}s waiting for observation > {seq}")
        time.sleep(0.1)


def show(seq, obs):
    st = obs["state"]
    print(f"[obs {seq}] env_step={obs.get('env_step')}  " + "  ".join(f"{k}={v:+.3f}" for k, v in st.items()))
    if obs.get("commanded"):
        diff = {k: st[k] - v for k, v in obs["commanded"].items()}
        worst = max(diff.items(), key=lambda kv: abs(kv[1]))
        print(f"   observed - commanded: max |{worst[0]}| = {abs(worst[1]):.3f}")
    for side, kin in (obs.get("kinematics") or {}).items():
        ee, rpy, tip = kin["ee_xyz"], kin["ee_rpy"], kin["tip_xyz"]
        print(f"   {side}: EE xyz=({ee[0]:+.3f},{ee[1]:+.3f},{ee[2]:+.3f}) rpy=({rpy[0]:+.2f},{rpy[1]:+.2f},{rpy[2]:+.2f})"
              f"  tip xyz=({tip[0]:+.3f},{tip[1]:+.3f},{tip[2]:+.3f})  tip↑table={kin['tip_above_table']*100:.1f}cm")
    if obs.get("approvals"):
        print("   guardrail interventions since last act():", obs["approvals"])
    if obs.get("operator_messages"):
        print("   operator:", obs["operator_messages"])
    for name, path in sorted((obs.get("images") or {}).items()):
        print(f"   image {name}: {path}")


def next_cmd_id(root: Path) -> int:
    ids = [int(f.stem) for f in (root / "cmd").glob("*.json") if f.stem.isdigit()]
    return (max(ids) if ids else 0) + 1


def send(root: Path, cmd: dict, obs_timeout: float):
    seq, obs = latest(root)
    cid = next_cmd_id(root)
    tmp = root / "cmd" / f"{cid:06d}.json.tmp"
    tmp.write_text(json.dumps(cmd, ensure_ascii=False))
    os.replace(tmp, root / "cmd" / f"{cid:06d}.json")
    reply_path = root / "reply" / f"{cid:06d}.json"
    t0 = time.time()
    while not reply_path.exists():
        if time.time() - t0 > 30:
            raise SystemExit("no reply from the policy in 30 s (is the eval running / waiting in act()?)")
        time.sleep(0.05)
    reply = read_json(reply_path)
    if not reply.get("ok"):
        print(f"[cmd {cid}] REJECTED: {reply.get('error')}")
        return 1
    desc = f"steps={reply.get('steps')}"
    if reply.get("seconds") is not None:
        desc += f" ({reply['seconds']}s)"
    if reply.get("target"):
        desc += "  target=" + " ".join(f"{k}={v:+.3f}" for k, v in reply["target"].items())
    print(f"[cmd {cid}] {cmd['op']} accepted: {desc}")
    tk = (reply.get("target_kinematics") or {}).get("left")
    if tk:
        print(f"   target EE=({tk['ee_xyz'][0]:+.3f},{tk['ee_xyz'][1]:+.3f},{tk['ee_xyz'][2]:+.3f}) pitch={tk['ee_rpy'][1]:+.2f} "
              f"tip↑table={tk['tip_above_table']*100:.1f}cm")
    if cmd["op"] in ("done", "give_up"):
        print("   (trial will end; the embodiment parks and releases the arm)")
        return 0
    wait = (reply.get("seconds") or 0.5) + obs_timeout
    seq2, obs2 = wait_obs_after(root, seq, wait)
    show(seq2, obs2)
    # 到位残差
    if reply.get("target"):
        res = {k: obs2["state"][k] - v for k, v in reply["target"].items()}
        worst = max(res.items(), key=lambda kv: abs(kv[1]))
        print(f"   residual vs target: max |{worst[0]}| = {abs(worst[1]):.3f} rad")
    return 0


def current_joints(obs, side="left"):
    """IK 种子 / rel 基准用上一次下发的目标（策略以它为插值起点），没有时退回观测值。"""
    st = obs.get("commanded") or obs["state"]
    return np.array([st[f"{side}_j{i}"] for i in range(6)], float)


def ik_targets(obs, xyz, roll, pitch, yaw, side="left", compensate=False):
    q0 = current_joints(obs, side)
    tgt = [xyz[0], xyz[1], xyz[2], roll, pitch, yaw]
    seed = q0.copy(); seed[0] = yaw; seed[4] = 0.0; seed[5] = 0.0   # 先试平面解（j0 = 目标偏航，腕偏航/横滚为 0）
    q, ep, er, ok = K.ik(tgt, seed)
    if not ok:
        q, ep, er, ok = K.ik(tgt, q0)
    fk = K.fk(q)
    print(f"   IK target xyzrpy={np.round(tgt,3).tolist()} -> q={np.round(q,3).tolist()} pos_err={ep*1000:.1f}mm rot_err={er:.3f} ok={ok}")
    print(f"   |dq| max = {np.abs(q-q0).max():.3f} rad;  FK check xyz={np.round(fk[:3],3).tolist()} rpy={np.round(fk[3:],2).tolist()}")
    if not ok:
        print("   IK did not converge — not sending"); return None
    viol = K.violations(q, margin=0.02)
    if viol:
        print("   joint-limit violations:", viol, "— not sending"); return None
    if compensate and obs.get("offset"):
        off = np.array([obs["offset"][f"{side}_j{i}"] for i in range(6)])
        q = q - off                                   # 静差前馈：让观测到的位姿落到目标上
        print(f"   compensated by static offset {np.round(-off,3).tolist()} -> q={np.round(q,3).tolist()}")
    return {f"{side}_j{i}": float(q[i]) for i in range(6)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mailbox", default=str(DEFAULT_MAILBOX))
    ap.add_argument("--obs-timeout", type=float, default=15.0, help="额外等待新观测的秒数（在播放时长之上）")
    sub = ap.add_subparsers(dest="op", required=True)
    sub.add_parser("state")
    sub.add_parser("hold")
    p = sub.add_parser("move"); p.add_argument("targets", nargs="+"); p.add_argument("--note", required=True); p.add_argument("--speed", type=float)
    p = sub.add_parser("ee"); p.add_argument("x", type=float); p.add_argument("y", type=float); p.add_argument("z", type=float)
    p.add_argument("--pitch", type=float, required=True); p.add_argument("--roll", type=float, default=0.0); p.add_argument("--yaw", type=float)
    p.add_argument("--note", required=True); p.add_argument("--speed", type=float); p.add_argument("--compensate", action="store_true")
    p = sub.add_parser("rel"); p.add_argument("dx", type=float); p.add_argument("dy", type=float); p.add_argument("dz", type=float)
    p.add_argument("--dpitch", type=float, default=0.0); p.add_argument("--note", required=True); p.add_argument("--speed", type=float); p.add_argument("--compensate", action="store_true")
    p = sub.add_parser("grip"); p.add_argument("value", type=float); p.add_argument("--note", required=True); p.add_argument("--speed", type=float)
    p = sub.add_parser("done"); p.add_argument("--note", required=True); p.add_argument("--hindsight")
    p = sub.add_parser("giveup"); p.add_argument("--note", required=True); p.add_argument("--hindsight")
    a = ap.parse_args()
    root = Path(os.path.expanduser(a.mailbox))

    if a.op == "state":
        show(*latest(root)); return 0
    if a.op == "hold":
        return send(root, {"op": "hold", "note": "fresh observation"}, a.obs_timeout)
    if a.op in ("done", "giveup"):
        cmd = {"op": "done" if a.op == "done" else "give_up", "note": a.note}
        if a.hindsight: cmd["hindsight"] = a.hindsight
        return send(root, cmd, a.obs_timeout)

    seq, obs = latest(root)
    if a.op == "move":
        targets = {}
        for kv in a.targets:
            k, _, v = kv.partition("=")
            targets[k] = float(v)
    elif a.op == "grip":
        targets = {"left_gripper": float(a.value)}
    else:
        if a.op == "ee":
            xyz = [a.x, a.y, a.z]; pitch = a.pitch; roll = a.roll
            # SDK 末端坐标以折叠位末端为原点，基座轴在其后方 P0.x；平面构型（j4=j5=0）的偏航 = 绕基座轴的方位角
            yaw = a.yaw if a.yaw is not None else math.atan2(a.y, a.x + float(K._P0[0]))
        else:
            fk = K.fk(current_joints(obs))          # 以上一次下发目标的 FK 为基准，避免把静差累积进去
            xyz = [fk[0] + a.dx, fk[1] + a.dy, fk[2] + a.dz]; roll = fk[3]; pitch = fk[4] + a.dpitch; yaw = fk[5]
        targets = ik_targets(obs, xyz, roll, pitch, yaw, compensate=a.compensate)
        if targets is None:
            return 1
    cmd = {"op": "move", "targets": targets, "note": a.note}
    if a.speed:
        cmd["speed_frac"] = a.speed
    return send(root, cmd, a.obs_timeout)


if __name__ == "__main__":
    sys.exit(main())
