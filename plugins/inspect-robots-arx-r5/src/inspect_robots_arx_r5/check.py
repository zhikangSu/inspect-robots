"""``inspect-robots-arx-r5-check``: verify the CAN link and conventions before any eval.

Read-only by default: connects to the configured arm(s), prints the seven
values each arm reports for a few seconds so you can move joints and the
gripper by hand and confirm which index is which and what the gripper reads
when open and closed. ``--wiggle`` adds one small, slow motion on the last
joint (and back), ``--gripper`` slowly closes and reopens the gripper, both
through the exact command path the embodiment uses.
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Any

import numpy as np

from inspect_robots_arx_r5 import packing
from inspect_robots_arx_r5.config import ArxR5Config
from inspect_robots_arx_r5.embodiment import ArxR5Embodiment


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arms", default="both", choices=sorted(packing.SIDES))
    parser.add_argument("--left-can", default="can1")
    parser.add_argument("--right-can", default="can3")
    parser.add_argument("--sdk-path", default=None, help="R5/py/ARX_R5_python (or $ARX_R5_PYTHON)")
    parser.add_argument("--arm-type", type=int, default=0)
    parser.add_argument("--gripper-open", type=float, default=4.0)
    parser.add_argument("--gripper-closed", type=float, default=0.0)
    parser.add_argument("--seconds", type=float, default=8.0, help="how long to print readings")
    parser.add_argument("--wiggle", action="store_true", help="move j5 by +0.05 rad and back")
    parser.add_argument("--gripper", action="store_true", help="close and reopen the gripper")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, embodiment: ArxR5Embodiment | None = None) -> int:
    """Entry point; ``embodiment`` is injectable for tests."""
    args = _parse(argv)
    cfg = ArxR5Config(
        arms=args.arms,
        left_can=args.left_can,
        right_can=args.right_can,
        sdk_path=args.sdk_path,
        arm_type=args.arm_type,
        gripper_open=args.gripper_open,
        gripper_closed=args.gripper_closed,
        unattended=True,
        ramp_secs=2.0,
    )
    emb = embodiment or ArxR5Embodiment(cfg)
    labels = packing.dim_labels(cfg.arms)
    print(f"arx_r5 check: arms={cfg.arms} left={cfg.left_can} right={cfg.right_can}")
    print("connecting (no motion)...")
    emb._drivers = emb._driver_factory(cfg)
    try:
        deadline = time.monotonic() + args.seconds
        print("readings: rad, gripper normalized (0 closed, 1 open).")
        print("Move joints by hand to identify them.")
        while time.monotonic() < deadline:
            state = emb._read_state()
            print(
                "  "
                + "  ".join(f"{lab}={val:+.3f}" for lab, val in zip(labels, state, strict=True))
            )
            time.sleep(0.5)
        if args.wiggle or args.gripper:
            print("hold the e-stop: the arm will move.")
            start = emb._read_state()
            target = start.copy()
            if args.wiggle:
                for side_index in range(len(packing.sides(cfg.arms))):
                    target[side_index * packing.ARM_WIDTH + packing.ARM_DOF - 1] += 0.05
            if args.gripper:
                for index in packing.gripper_indices(cfg.arms):
                    target[index] = 0.0
            emb._ramp_to(np.clip(target, cfg.low, cfg.high))
            time.sleep(1.0)
            after = emb._read_state()
            print(
                "  moved:   "
                + "  ".join(f"{lab}={val:+.3f}" for lab, val in zip(labels, after, strict=True))
            )
            emb._ramp_to(start)
            print("  restored to the starting pose")
        return 0
    finally:
        drivers: dict[str, Any] = emb._drivers or {}
        for driver in drivers.values():
            driver.protect()
            driver.close()
        emb._drivers = None


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
