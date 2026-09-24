#!/usr/bin/env python3
"""R5 从臂（X5liteaa0.urdf）正/逆运动学，纯 numpy，用于下发前预检关节解和限位。

末端参考系与 SDK get_ee_pose 一致：位置 = link6 原点相对零位 link6 原点（基座系），姿态 = link6 在基座系的 rpy。
（2026-09-13 实测：SDK 上报关节 + 本 FK 与 SDK 上报末端吻合到 1e-4。）

关节限位（SDK/URDF 符号）：手册表 J2 [-3.6,0.1] 与实测符号相反（抬臂 J2 为正），故取镜像 [-0.1,3.6]；
J1 手册 [-3.14,2.6] 符号未验证，取保守对称 ±2.6；其余对称。
"""
import numpy as np

JOINT_LIMITS = [(-2.6, 2.6), (-0.1, 3.6), (-1.57, 2.2), (-1.3, 1.3), (-1.57, 1.57), (-2.1, 2.1)]   # J3 上限 2026-09-14 经用户同意放宽到 2.2（肘 126°），理由见 AGENTS.md

_CHAIN = [((0, 0, 0.0565), (0, 0, 0), 'z'), ((0.02, 0, 0.047), (0, 0, 0), 'y'), ((-0.264, 0, 0), (np.pi, 0, 0), 'y'),
          ((0.245, 0, -0.06), (0, 0, 0), 'y'), ((0.06775, -5e-5, -0.0865), (0, 0, 0), 'z'), ((0.02895, 0, 0.0865), (np.pi, 0, 0), 'x')]


def _rx(a): c, s = np.cos(a), np.sin(a); return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])
def _ry(a): c, s = np.cos(a), np.sin(a); return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])
def _rz(a): c, s = np.cos(a), np.sin(a); return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])
_ROT = {'x': _rx, 'y': _ry, 'z': _rz}


def rpy2R(r, p, y):
    return _rz(y) @ _ry(p) @ _rx(r)


def R2rpy(R):
    return np.array([np.arctan2(R[2, 1], R[2, 2]), np.arcsin(np.clip(-R[2, 0], -1, 1)), np.arctan2(R[1, 0], R[0, 0])])


def fk_T(q):
    T = np.eye(4)
    for (xyz, rpy, ax), qi in zip(_CHAIN, q):
        Tj = np.eye(4); Tj[:3, :3] = rpy2R(*rpy); Tj[:3, 3] = xyz
        Tq = np.eye(4); Tq[:3, :3] = _ROT[ax](qi)
        T = T @ Tj @ Tq
    return T


_P0 = fk_T([0] * 6)[:3, 3]


def fk(q):
    """关节 -> 末端 xyzrpy（SDK 约定）。"""
    T = fk_T(q)
    return np.concatenate([T[:3, 3] - _P0, R2rpy(T[:3, :3])])


def _rotvec(R):
    ang = np.arccos(np.clip((np.trace(R) - 1) / 2, -1, 1))
    if ang < 1e-9:
        return np.zeros(3)
    return ang / (2 * np.sin(ang)) * np.array([R[2, 1] - R[1, 2], R[0, 2] - R[2, 0], R[1, 0] - R[0, 1]])


def _err(q, p_t, R_t):
    T = fk_T(q)
    return np.concatenate([T[:3, 3] - _P0 - p_t, _rotvec(R_t @ T[:3, :3].T)])


def ik(xyzrpy, q0, iters=150, lam=0.05, tol_p=1e-4, tol_r=1e-3):
    """阻尼最小二乘数值 IK，从 q0 出发。返回 (q, pos_err, rot_err, converged)。"""
    p_t = np.asarray(xyzrpy[:3], float)
    R_t = rpy2R(*xyzrpy[3:6])
    q = np.array(q0, float)[:6].copy()
    for _ in range(iters):
        e = _err(q, p_t, R_t)
        if np.linalg.norm(e[:3]) < tol_p and np.linalg.norm(e[3:]) < tol_r:
            break
        J = np.zeros((6, 6))
        h = 1e-6
        for j in range(6):
            dq = np.zeros(6); dq[j] = h
            J[:, j] = (_err(q + dq, p_t, R_t) - e) / h
        dq = -np.linalg.solve(J.T @ J + lam ** 2 * np.eye(6), J.T @ e)
        step = np.linalg.norm(dq)
        if step > 0.3:
            dq *= 0.3 / step
        q += dq
    e = _err(q, p_t, R_t)
    ep, er = float(np.linalg.norm(e[:3])), float(np.linalg.norm(e[3:]))
    return q, ep, er, (ep < 5e-4 and er < 5e-3)


def _margins(margin):
    return list(margin) if isinstance(margin, (list, tuple)) else [margin] * 6


def violations(q, margin=0.05):
    """margin 可为标量或 6 元列表（逐关节余量）。"""
    out = []
    for j, ((lo, hi), v, m) in enumerate(zip(JOINT_LIMITS, q, _margins(margin))):
        if v < lo + m or v > hi - m:
            out.append(f"J{j+1}={v:.3f} 超出 [{lo+m:.2f},{hi-m:.2f}]")
    return out


def check_path(q_start, start_xyzrpy, target_xyzrpy, step=0.01, rot_step=0.05, margin=0.05):
    """沿直线路径逐点求 IK，检查收敛、限位、相邻路点关节跳变。返回 dict。"""
    s, t = np.asarray(start_xyzrpy, float), np.asarray(target_xyzrpy, float)
    drot = (t[3:] - s[3:] + np.pi) % (2 * np.pi) - np.pi
    n = max(1, int(np.ceil(np.linalg.norm(t[:3] - s[:3]) / step)), int(np.ceil(np.abs(drot).max() / rot_step)))
    q = np.array(q_start, float)[:6]
    qs, max_jump = [], 0.0
    for i in range(1, n + 1):
        a = i / n
        wp = np.concatenate([s[:3] + a * (t[:3] - s[:3]), s[3:] + a * drot])
        q_new, ep, er, ok = ik(wp, q)
        if not ok:
            return {"ok": False, "err": f"路点 {i}/{n} IK 不收敛 (pos {ep*1000:.1f} mm, rot {er:.3f} rad)", "wp": wp.tolist(), "q": q_new.tolist()}
        v = violations(q_new, margin)
        if v:
            return {"ok": False, "err": f"路点 {i}/{n} 关节越限: " + "; ".join(v), "wp": wp.tolist(), "q": q_new.tolist()}
        jump = float(np.abs(q_new - q).max())
        if jump > 0.6:
            return {"ok": False, "err": f"路点 {i}/{n} 关节跳变 {jump:.2f} rad（疑似换支/奇异）", "wp": wp.tolist(), "q": q_new.tolist(), "q_prev": q.tolist()}
        max_jump = max(max_jump, jump)
        q = q_new
        qs.append(q.tolist())
    return {"ok": True, "n": n, "q_end": q.tolist(), "max_jump": round(max_jump, 3)}


if __name__ == "__main__":
    import sys, json
    # 用法: python3 r5_kin.py fk q1..q6 | ik x y z r p y [q0...] | path x y z r p y  x2 y2 z2 r2 p2 y2 [q0...]
    a = sys.argv[1:]
    if a and a[0] == "fk":
        print(np.round(fk([float(v) for v in a[1:7]]), 4).tolist())
    elif a and a[0] == "ik":
        q0 = [float(v) for v in a[7:13]] if len(a) >= 13 else [0] * 6
        q, ep, er, ok = ik([float(v) for v in a[1:7]], q0)
        print(json.dumps({"q": np.round(q, 4).tolist(), "pos_err_mm": round(ep * 1000, 2), "rot_err": round(er, 4), "ok": ok, "violations": violations(q)}, ensure_ascii=False))
    elif a and a[0] == "path":
        q0 = [float(v) for v in a[13:19]] if len(a) >= 19 else [0] * 6
        print(json.dumps(check_path(q0, [float(v) for v in a[1:7]], [float(v) for v in a[7:13]]), ensure_ascii=False))
