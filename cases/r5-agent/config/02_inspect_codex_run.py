#!/usr/bin/env python3
"""Run a supervised Codex mailbox policy through Inspect Robots on can1.

Reset only observes. Folding is an explicit reviewed policy motion. Interrupts
stop issuing targets and keep the SDK alive holding its last position; they do
not trigger the stock adapter's unconditional parking ramp.
"""

import argparse
import dataclasses
import json
import os
from pathlib import Path
import signal
import sys
import time

import numpy as np

from inspect_robots import eval as ir_eval
from inspect_robots.approver import ChainApprover, ClampApprover, DeltaLimitApprover
from inspect_robots.defaults import load_defaults
from inspect_robots.errors import SafetyAbort
from inspect_robots.scene import Scene
from inspect_robots.scorer import episode_length
from inspect_robots.task import Task
from inspect_robots_arx_r5 import ArxR5Config, ArxR5Embodiment
from inspect_robots_arx_r5._cameras import OpenCVCameraReader
from inspect_claude_driver.policy import ClaudeDriverPolicy, TableCheck
import r5_kin as K


class CalibratedCameras(OpenCVCameraReader):
    """Pin native wrist 640x480 and fixed 800x600 before delivery resizing."""

    def _open_all(self):
        super()._open_all()
        cv = self._cv2
        for name, cap in self._caps.items():
            width, height = (800, 600) if name == 'fixed' else (640, 480)
            cap.set(cv.CAP_PROP_FOURCC, cv.VideoWriter_fourcc(*'YUYV'))
            cap.set(cv.CAP_PROP_FRAME_WIDTH, width)
            cap.set(cv.CAP_PROP_FRAME_HEIGHT, height)
            if (cap.get(cv.CAP_PROP_FRAME_WIDTH), cap.get(cv.CAP_PROP_FRAME_HEIGHT)) != (width, height):
                raise RuntimeError(f'{name}: native camera resolution is not {width}x{height}')
            for _ in range(8):
                if not cap.grab():
                    raise RuntimeError(f'{name}: camera warmup failed')


class ObservedStartArm(ArxR5Embodiment):
    """Retain the SDK path while guarding every actual target and lifecycle."""

    def __init__(self, cfg, **kwargs):
        super().__init__(cfg, **kwargs)
        self.check = TableCheck(str(Path(__file__).parent), table_z=-0.1635,
                                tip_len=0.19, min_tip_clearance=0.008,
                                min_ee_clearance=0.06, max_reach=0.50)
        self.last_sent = None

    def reset(self, scene, *, seed=None):
        self._drivers = self._driver_factory(self._cfg)
        self._sleep(1.0)  # Initial SDK reads can be zero before motor replies arrive.
        self._instruction = scene.instruction
        self._init_pose = self._read_state()
        self.last_sent = self._init_pose.copy()
        self._t_last = self._clock()
        self.num_steps = 0
        return self._observe(scene.instruction)

    def _send(self, cmd):
        cmd = np.asarray(cmd, dtype=float)
        actual = self._read_state()
        if cmd.shape != (7,) or not np.isfinite(cmd).all():
            raise SafetyAbort('Invalid command vector')
        if np.any(cmd < self._cfg.low) or np.any(cmd > self._cfg.high):
            raise SafetyAbort('Command outside configured joint bounds')
        if np.max(np.abs(cmd[:6] - actual[:6])) > 0.16:
            raise SafetyAbort('Command/measured joint error exceeds 0.16 rad')
        if self.last_sent is not None:
            if np.max(np.abs(cmd[:6] - self.last_sent[:6])) > 0.011:
                raise SafetyAbort('Command jump exceeds 0.011 rad')
        for label, q in [('target', cmd[:6]), ('measured', actual[:6])]:
            reason = self.check.reject(q)
            if reason:
                raise SafetyAbort(f'{label}: {reason}')
            # Both open jaw tips, including the lower jaw when the wrist rolls.
            T = K.fk_T(q)
            h = self.check.pose(q)['tip_above_table'] - 0.045 * abs(T[2, 1])
            if h < 0.006:
                raise SafetyAbort(f'{label}: lower jaw clearance {h:.4f} m')
        sent = super()._send(cmd)
        self.last_sent = sent.copy()
        return sent

    def close(self):
        # Never use the base class's automatic straight-line parking path.
        self._init_pose = None
        super().close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--fake', action='store_true')
    p.add_argument('--run-dir', required=True)
    a = p.parse_args()
    root = Path(a.run_dir).resolve()
    root.mkdir(parents=True, exist_ok=False)
    # Keep native C++ output away from the control console.
    saved_out, saved_err = os.dup(1), os.dup(2)
    fd = os.open(root / 'sdk.log', os.O_WRONLY | os.O_CREAT, 0o600)
    os.dup2(fd, 1); os.dup2(fd, 2); os.close(fd)
    sys.stdout = os.fdopen(saved_out, 'w', buffering=1)
    sys.stderr = os.fdopen(saved_err, 'w', buffering=1)
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    d = load_defaults(os.environ)
    cfg = ArxR5Config.from_kwargs(**d.embodiment_args).replace(
        unattended=True, park_before_grade=False, auto_start=False)
    if (cfg.arms, cfg.left_can, cfg.arm_type) != ('left', 'can1', 0):
        raise ValueError('This runner is restricted to the left follower on can1, type=0')
    kwargs = dict(camera_reader=CalibratedCameras(cfg.camera_devices, width=640, height=480))
    if a.fake:
        from inspect_dryrun import FakeArm, fake_cameras
        kwargs = dict(driver_factory=lambda _: {'left': FakeArm()},
                      camera_reader=fake_cameras(list(cfg.camera_devices), 480, 640))
    emb = ObservedStartArm(cfg, **kwargs)
    policy = ClaudeDriverPolicy(mailbox=str(root / 'mailbox'), max_speed_frac=0.008,
                                tip_len=0.19, min_tip_clearance=0.008)
    task = Task(name='r5-codex-cube-to-plate', scenes=[Scene(id='cube-to-blue-plate',
                instruction='Pick up the wooden cube and place it fully inside the blue plate. '
                'Verify a retained grasp by lifting before transport and verify release visually.')],
                scorer=episode_length(), max_steps=5000)
    (root / 'run.json').write_text(json.dumps({'pid': os.getpid(), 'fake': a.fake,
        'driver': 'current Codex via existing claude mailbox policy',
        'cfg': dataclasses.asdict(cfg), 'created': time.time()}, indent=2))
    print(f'RUN_DIR={root}\nPID={os.getpid()}\nReset observes only; no automatic home or park.', flush=True)
    space = emb.info.action_space
    exit_code = 1
    try:
        (log,) = ir_eval(task, policy, emb, log_dir=str(root / 'eval'),
            approver=ChainApprover(ClampApprover(space), DeltaLimitApprover(space)),
            store_frames=True, store_actions=True)
        print(f'EVAL_ENDED status={log.status} error={log.error}', flush=True)
        exit_code = 0 if log.status == 'success' else 1
    except BaseException as exc:
        print(f'CONTROL_STOPPED {type(exc).__name__}: {exc}', flush=True)
        exit_code = 130 if isinstance(exc, KeyboardInterrupt) else 1
    finally:
        if emb._drivers is not None:
            q = emb._read_state()
            folded = bool(np.max(np.abs(q[:6])) < 0.15)
            if not folded and not a.fake:
                print('HOLDING: arm remains supported at its last command. No automatic folding. '
                      'Supervise the arm. After physically supporting/powering off the arm, '
                      'create release_after_support in RUN_DIR to release the SDK.', flush=True)
                (root / 'holding.json').write_text(json.dumps({'state': q.tolist(), 'time': time.time()}))
                while not (root / 'release_after_support').exists():
                    try:
                        time.sleep(0.2)
                    except KeyboardInterrupt:
                        print('Still holding; physical support is required before SDK release.', flush=True)
            emb.close()
        print('SDK_RELEASED', flush=True)
    return exit_code


if __name__ == '__main__':
    sys.exit(main())
