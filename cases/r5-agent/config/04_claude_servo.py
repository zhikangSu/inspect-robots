#!/usr/bin/env python3
"""腕部相机视觉伺服（配合 claude_cmd.py / inspect-robots claude 策略）：把木块对到夹爪抓取线。

几何模型（全部来自 AGENTS.md 的实测标定 + r5_kin FK）：
  工具系 = link6 系（x 指向夹爪前方，j5 绕它横滚）；指尖闭合中心 = EE + TIP_LEN·x_t。
  相机光轴 cam_z = cos33°·x_t − sin33°·z_t（相对工具轴向下俯 33°，俯仰 1.0 时光轴垂直桌面），
  图像右 cam_x = −y_t，图像下 cam_y = cam_z × cam_x；内参 f=386, pp=(321,236)（640×480）。
  抓取线：相机系 (GX, GY, Z)，指尖在 Z=GZ 处 → 相机位置 = 指尖 − (GX·cam_x + GY·cam_y + GZ·cam_z)。
用法：
  python3 claude_servo.py                 # 只算：木块像素/世界位置、对准所需平移、当前指尖高度
  python3 claude_servo.py --go            # 发 ee 命令做对准平移（保持当前姿态）
  python3 claude_servo.py --go --tip H    # 对准 + 沿光轴下降到指尖离桌 H 米（可分多次，每次 ≤ --max-dz）
"""

import argparse
import json
import math
import os
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import r5_kin as K  # noqa: E402

MAILBOX = Path(os.path.expanduser("~/R5/logs/claude_driver"))
F, PPX, PPY = 386.0, 321.1, 235.7
TILT = math.radians(33.0)
TIP_LEN = 0.19          # 2026-09-19 实测：EE z=-0.006、俯仰 0.96 时指尖触桌 → 约 0.19（原估 0.15 偏短 4 cm）
GX, GY, GZ = 0.010, 0.024, 0.120
TABLE_Z = -0.1635
CUBE_H = 0.034


def latest_obs(seq=None):
    if seq is not None:
        return seq, json.loads((MAILBOX / "obs" / f"{seq:06d}.json").read_text())
    meta = json.loads((MAILBOX / "latest.json").read_text())
    return meta["seq"], json.loads(Path(meta["obs"]).read_text())


def camera_frame(q6):
    """返回 cam_pos, cam_x, cam_y, cam_z, tip（基座系）。"""
    T = K.fk_T(q6)
    p = T[:3, 3] - K._P0
    x_t, y_t, z_t = T[:3, 0], T[:3, 1], T[:3, 2]
    tip = p + TIP_LEN * x_t
    cam_z = math.cos(TILT) * x_t - math.sin(TILT) * z_t
    cam_x = -y_t
    cam_y = np.cross(cam_z, cam_x)
    cam_pos = tip - (GX * cam_x + GY * cam_y + GZ * cam_z)
    return cam_pos, cam_x, cam_y, cam_z, tip


def pixel_to_plane(px, cam_pos, cam_x, cam_y, cam_z, plane_z):
    u, v = px
    d = cam_z + (u - PPX) / F * cam_x + (v - PPY) / F * cam_y
    if abs(d[2]) < 1e-6:
        return None
    t = (plane_z - cam_pos[2]) / d[2]
    return cam_pos + t * d if t > 0 else None


def plan_pose(C, roll, pitch, tip_clearance, iters=4):
    """给定木块顶面中心 C（世界）、目标横滚/俯仰、指尖离桌高度，求平面构型（j4=j5 补偿≈0）下
    抓取线穿过 C 的 EE 位置与 rpy。返回 (xyz, rpy)。"""
    yaw = math.atan2(C[1], C[0] + float(K._P0[0]))
    for _ in range(iters):
        R = K.rpy2R(roll, pitch, yaw)
        x_t, z_t = R[:, 0], R[:, 2]
        cam_z = math.cos(TILT) * x_t - math.sin(TILT) * z_t
        k = (C[2] - TABLE_Z - tip_clearance) / cam_z[2]     # 沿光轴从 C 退回到指尖高度
        P = C - k * cam_z                                    # 指尖闭合中心
        ee = P - TIP_LEN * x_t
        yaw = math.atan2(ee[1], ee[0] + float(K._P0[0]))
    return ee, np.array([roll, pitch, yaw])


def detect_cube(img_bgr, v_thr=85, s_thr=45):
    """木块顶面：在 D405 白平衡下是暗且低饱和的冷灰色（V≈70, S≈15），而阴影和侧面是暖色高饱和（S 50–100），
    手指是高饱和的黑（S≈200），托盘偏蓝——所以用 V<v_thr 且 S<s_thr 且非蓝，再排除画面下方两角，取最接近正方形的块。"""
    hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
    sat, v = hsv[:, :, 1], hsv[:, :, 2]
    b, g, r = img_bgr[:, :, 0].astype(int), img_bgr[:, :, 1].astype(int), img_bgr[:, :, 2].astype(int)
    blue = (b - r) > 25
    mask = ((v < v_thr) & (sat < s_thr) & ~blue).astype(np.uint8)
    h, w = mask.shape
    mask[int(h * 0.52):, : int(w * 0.36)] = 0     # 左指
    mask[int(h * 0.52):, int(w * 0.72):] = 0      # 右指
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best = None
    for c in cnts:
        area = cv2.contourArea(c)
        if not 400 < area < 40000:
            continue
        (cx, cy), (bw, bh), ang = cv2.minAreaRect(c)
        if min(bw, bh) < 1:
            continue
        aspect = max(bw, bh) / min(bw, bh)
        fill = area / (bw * bh)
        score = aspect + 2 * (1 - fill)              # 越接近实心正方形越好
        if aspect < 2.2 and fill > 0.5 and (best is None or score < best["score"]):
            best = {"px": (float(cx), float(cy)), "size": (float(bw), float(bh)), "angle": float(ang),
                    "area": float(area), "score": float(score), "contour": c}
    return best


def top_face_center(det, face_px):
    """顶面中心的顶边锚定估计：暗块最上沿 + 半个顶面边长；横向取顶面那些行的中点。
    侧面和阴影都在顶面下方，不影响最上沿。"""
    c = det["contour"][:, 0, :]
    top = int(c[:, 1].min())
    rows = c[(c[:, 1] >= top) & (c[:, 1] <= top + face_px)]
    cx = float((rows[:, 0].min() + rows[:, 0].max()) / 2) if len(rows) else det["px"][0]
    return (cx, top + face_px / 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--go", action="store_true")
    ap.add_argument("--tip", type=float, help="目标指尖离桌高度（米），沿光轴下降")
    ap.add_argument("--max-dz", type=float, default=0.03, help="单次沿光轴下降上限（米）")
    ap.add_argument("--px", type=float, nargs=2, help="手动指定木块像素 (u v)，跳过检测")
    ap.add_argument("--plan", type=float, nargs=3, metavar=("ROLL", "PITCH", "TIP"),
                    help="不做平移伺服，而是按目标横滚/俯仰和指尖离桌高度反算 EE 位姿（平面构型）")
    ap.add_argument("--note", default="")
    ap.add_argument("--obs", type=int, help="用指定序号的观测（默认最新）")
    ap.add_argument("--compensate", action="store_true", help="把观测到的静差前馈进关节指令（claude_cmd.py ee --compensate）")
    a = ap.parse_args()

    seq, obs = latest_obs(a.obs)
    q_obs = np.array([obs["state"][f"left_j{i}"] for i in range(6)])
    q_cmd = np.array([(obs.get("commanded") or obs["state"])[f"left_j{i}"] for i in range(6)])
    cam_pos, cam_x, cam_y, cam_z, tip = camera_frame(q_obs)          # 图像是在观测姿态下拍的
    tilt_from_vertical = math.degrees(math.acos(-cam_z[2]))
    print(f"[obs {seq}] tip=({tip[0]:+.3f},{tip[1]:+.3f},{tip[2]:+.3f}) tip↑table={(tip[2]-TABLE_Z)*100:.1f}cm  "
          f"cam height={(cam_pos[2]-TABLE_Z)*100:.1f}cm  optical axis {tilt_from_vertical:.1f}° from vertical")
    img = cv2.imread(obs["images"]["wrist"])
    if a.px:
        det = {"px": tuple(a.px), "size": (0, 0), "angle": 0, "area": 0}
    else:
        det = detect_cube(img)
        if det is None:
            print("cube not found"); return 1
    print(f"   cube blob px=({det['px'][0]:.0f},{det['px'][1]:.0f}) size=({det['size'][0]:.0f}x{det['size'][1]:.0f}) angle={det['angle']:.1f}")
    top_z = TABLE_Z + CUBE_H
    C = pixel_to_plane(det["px"], cam_pos, cam_x, cam_y, cam_z, top_z)
    if C is None:
        print("ray does not hit the cube-top plane"); return 1
    Z = float(np.dot(C - cam_pos, cam_z))
    if not a.px and det.get("contour") is not None:
        face_px = F * CUBE_H / Z
        px2 = top_face_center(det, face_px)
        C2 = pixel_to_plane(px2, cam_pos, cam_x, cam_y, cam_z, top_z)
        if C2 is not None:
            det["px"] = px2
            C = C2
            Z = float(np.dot(C - cam_pos, cam_z))
            print(f"   top-anchored top face center px=({px2[0]:.0f},{px2[1]:.0f}) (face {face_px:.0f} px)")
    G = cam_pos + GX * cam_x + GY * cam_y + Z * cam_z          # 抓取线上同深度的点
    delta = C - G
    print(f"   cube top center (world)=({C[0]:+.3f},{C[1]:+.3f},{C[2]:+.3f})  depth Z={Z:.3f}")
    print(f"   grasp line at that depth=({G[0]:+.3f},{G[1]:+.3f},{G[2]:+.3f})  -> align translation Δ=({delta[0]:+.4f},{delta[1]:+.4f},{delta[2]:+.4f})  |Δ|={np.linalg.norm(delta)*100:.1f}cm")
    if a.plan:
        roll, pitch, h = a.plan
        ee, rpy = plan_pose(C, roll, pitch, h)
        print(f"   plan: roll={roll:+.2f} pitch={pitch:+.2f} tip↑table={h*100:.1f}cm -> EE=({ee[0]:+.3f},{ee[1]:+.3f},{ee[2]:+.3f}) yaw={rpy[2]:+.3f}")
        if not a.go:
            return 0
        note = a.note or (f"plan: cube top center at ({C[0]:+.3f},{C[1]:+.3f}) from px ({det['px'][0]:.0f},{det['px'][1]:.0f}); "
                          f"go above it with roll {roll:+.2f}, pitch {pitch:+.2f}, fingertips {h*100:.0f} cm above the table")
        cmd = [sys.executable, str(HERE / "claude_cmd.py"), "ee", f"{ee[0]:.4f}", f"{ee[1]:.4f}", f"{ee[2]:.4f}",
               "--pitch", f"{pitch:.4f}", "--roll", f"{roll:.4f}", "--yaw", f"{rpy[2]:.4f}", "--note", note] + (["--compensate"] if a.compensate else [])
        return subprocess.call(cmd)
    move = delta.copy()
    if a.tip is not None:
        want = a.tip - (tip[2] - TABLE_Z)                       # 指尖需要的高度变化（负 = 下降）
        # 沿光轴前进 s（s>0 = 朝前/朝下）使指尖高度变化 s*cam_z[2]；光轴接近水平时退化为直接竖直移动
        s = want / cam_z[2] if cam_z[2] < -0.3 else -want      # cam_z 朝下时 cam_z[2]<0，want<0 → s>0 前进
        s = float(np.clip(s, -a.max_dz, a.max_dz))
        move = delta + s * cam_z
        print(f"   descend along optical axis s={s*100:+.1f}cm (want tip Δz={want*100:+.1f}cm, this step Δz={s*cam_z[2]*100:+.1f}cm)")
    # 基准：静差前馈模式下用观测位姿（相机就是在这个位姿拍的，目标 = 观测 + 位移，指令由 ee --compensate 反推）；
    # 否则用上一次下发目标的 FK，避免把静差累积进指令
    fk_cmd = K.fk(q_obs) if a.compensate else K.fk(q_cmd)
    target = fk_cmd[:3] + move
    print(f"   EE target=({target[0]:+.3f},{target[1]:+.3f},{target[2]:+.3f}) rpy(keep)=({fk_cmd[3]:+.2f},{fk_cmd[4]:+.2f},{fk_cmd[5]:+.2f})")
    if not a.go:
        return 0
    note = a.note or f"servo: cube at px ({det['px'][0]:.0f},{det['px'][1]:.0f}), {np.linalg.norm(delta)*100:.1f} cm off the grasp line; translate by Δ=({move[0]:+.3f},{move[1]:+.3f},{move[2]:+.3f}) keeping orientation"
    cmd = [sys.executable, str(HERE / "claude_cmd.py"), "ee", f"{target[0]:.4f}", f"{target[1]:.4f}", f"{target[2]:.4f}",
           "--pitch", f"{fk_cmd[4]:.4f}", "--roll", f"{fk_cmd[3]:.4f}", "--yaw", f"{fk_cmd[5]:.4f}", "--note", note] + (["--compensate"] if a.compensate else [])
    return subprocess.call(cmd)


if __name__ == "__main__":
    sys.exit(main())
