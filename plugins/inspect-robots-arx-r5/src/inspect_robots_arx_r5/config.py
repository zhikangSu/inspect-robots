"""Configuration for the ARX R5 embodiment.

Every field can be set from Python (``ArxR5Config(...)``), from the CLI
(``-E key=value``), or from ``[embodiment.args]`` in ``config.ini``. Values
that are tuples in Python are written as comma-separated strings on the CLI.

Defaults are deliberately conservative and several MUST be verified on the
rig before the first run (see the README): joint bounds, the gripper's native
open/closed readings, and the home pose.
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from typing import Any

import numpy as np
import numpy.typing as npt

from inspect_robots.errors import ConfigError
from inspect_robots_arx_r5 import packing

_SIX_PI = (math.pi,) * packing.ARM_DOF
_SIX_ZERO = (0.0,) * packing.ARM_DOF


def _floats(value: Any, n: int, name: str) -> tuple[float, ...]:
    """Coerce a tuple/list or a comma-separated string into ``n`` floats."""
    if isinstance(value, str):
        parts = [p.strip() for p in value.split(",") if p.strip()]
    else:
        try:
            parts = list(value)
        except TypeError:
            raise ConfigError(
                f"{name} must be {n} comma-separated numbers, got {value!r}"
            ) from None
    try:
        out = tuple(float(p) for p in parts)
    except (TypeError, ValueError):
        raise ConfigError(f"{name} must be {n} numbers, got {value!r}") from None
    if len(out) != n or not all(math.isfinite(v) for v in out):
        raise ConfigError(f"{name} must be {n} finite numbers, got {value!r}")
    return out


def parse_cameras(spec: str | Mapping[str, str] | None) -> dict[str, str]:
    """Parse ``name:/dev/videoN,name2:/dev/videoM`` (or a mapping) into a dict."""
    if spec is None:
        return {}
    if isinstance(spec, Mapping):
        return {str(k): str(v) for k, v in spec.items()}
    out: dict[str, str] = {}
    for entry in spec.split(","):
        entry = entry.strip()
        if not entry:
            continue
        name, sep, device = entry.partition(":")
        if not sep or not name.strip() or not device.strip():
            raise ConfigError(
                f"cameras entry {entry!r} must look like name:/dev/videoN\n"
                "fix: -E cameras=top:/dev/video0,wrist:/dev/video2"
            )
        if name.strip() in out:
            raise ConfigError(f"duplicate camera name {name.strip()!r} in cameras")
        out[name.strip()] = device.strip()
    return out


@dataclass(frozen=True)
class ArxR5Config:
    """Rig-level settings for one or two ARX R5 arms driven over CAN.

    ``arms`` selects ``both`` (14-D, left then right), ``left`` or ``right``
    (7-D). ``left_can``/``right_can`` are the SocketCAN interfaces the ARX
    ``arx_canN.sh`` scripts bring up (ARX's VR/dual-arm convention is
    ``can1`` for the left follower and ``can3`` for the right one).
    ``sdk_path`` is the directory that contains the SDK's ``bimanual``
    package (``.../R5/py/ARX_R5_python`` after ``./build.sh``); it defaults
    to ``$ARX_R5_PYTHON``. ``arm_type`` is forwarded to the SDK's
    ``SingleArm({"type": ...})`` and selects the end-effector variant.

    ``joint_low``/``joint_high`` bound the six revolute joints (radians) of
    each arm; the same bounds apply to both arms. ``joint_max_step`` is the
    largest per-control-step change (radians) the default delta limiter
    allows on every joint. ``gripper_closed``/``gripper_open`` are the SDK's
    native gripper readings at the two extremes; the wire uses ``0``/``1``.
    ``gripper_max_step`` is the per-step change in normalized units.

    ``home_joints``/``home_gripper`` is the pose every trial starts from, and
    ``rest_joints``/``rest_gripper`` where ``close()`` and the grading park
    go (``None`` parks back to the pose captured at the first ``reset()``).
    Ramps take ``ramp_secs`` at ``control_hz``.

    ``cameras`` lists V4L2 devices as ``name:/dev/videoN,...``; frames are
    delivered as ``cam_height`` x ``cam_width`` RGB. ``unattended`` skips
    every operator prompt; ``auto_start`` skips only the stand-clear and
    scene-ready prompts while keeping the console. ``park_before_grade``
    moves the arms to rest before the grader looks at the final frames.
    ``docs_extra`` is appended verbatim to the notes the LLM agent reads.
    """

    arms: str = "both"
    left_can: str = "can1"
    right_can: str = "can3"
    arm_type: int = 0
    sdk_path: str | None = None
    control_hz: float = 20.0
    joint_low: tuple[float, ...] = tuple(-v for v in _SIX_PI)
    joint_high: tuple[float, ...] = _SIX_PI
    joint_max_step: float = 0.1
    gripper_closed: float = 0.0
    gripper_open: float = 4.0
    gripper_max_step: float | None = 0.2
    home_joints: tuple[float, ...] = _SIX_ZERO
    home_gripper: float = 1.0
    rest_joints: tuple[float, ...] | None = None
    rest_gripper: float = 1.0
    ramp_secs: float = 3.0
    cameras: str = ""
    cam_width: int = 640
    cam_height: int = 480
    unattended: bool = False
    auto_start: bool = False
    park_before_grade: bool = True
    docs_extra: str = ""
    camera_devices: dict[str, str] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_kwargs(cls, **flat: Any) -> ArxR5Config:
        """Build a config from flat ``key=value`` pairs, rejecting unknown keys."""
        known = {f.name for f in fields(cls)} - {"camera_devices"}
        unknown = sorted(set(flat) - known)
        if unknown:
            raise ConfigError(
                f"unknown arx_r5 option(s): {', '.join(unknown)}\n"
                f"fix: use one of {', '.join(sorted(known))}"
            )
        return cls(**flat)

    def __post_init__(self) -> None:
        packing.sides(self.arms)
        if isinstance(self.control_hz, bool) or not (
            math.isfinite(self.control_hz) and self.control_hz > 0
        ):
            raise ConfigError(f"control_hz must be finite and > 0, got {self.control_hz!r}")
        if self.arms in ("both", "left") and not self.left_can:
            raise ConfigError("left_can must name a CAN interface, e.g. can1")
        if self.arms in ("both", "right") and not self.right_can:
            raise ConfigError("right_can must name a CAN interface, e.g. can3")
        if self.arms == "both" and self.left_can == self.right_can:
            raise ConfigError("left_can and right_can must differ")
        low = _floats(self.joint_low, packing.ARM_DOF, "joint_low")
        high = _floats(self.joint_high, packing.ARM_DOF, "joint_high")
        if any(lo >= hi for lo, hi in zip(low, high, strict=True)):
            raise ConfigError(f"joint_low must be < joint_high per joint, got {low} / {high}")
        object.__setattr__(self, "joint_low", low)
        object.__setattr__(self, "joint_high", high)
        if not (math.isfinite(self.joint_max_step) and self.joint_max_step > 0):
            raise ConfigError(f"joint_max_step must be > 0, got {self.joint_max_step!r}")
        if not (math.isfinite(self.gripper_open) and math.isfinite(self.gripper_closed)):
            raise ConfigError("gripper_open and gripper_closed must be finite")
        if self.gripper_open == self.gripper_closed:
            raise ConfigError("gripper_open and gripper_closed must differ")
        if self.gripper_max_step is not None and not (
            math.isfinite(self.gripper_max_step) and 0 < self.gripper_max_step <= 1
        ):
            raise ConfigError(f"gripper_max_step must be in (0, 1], got {self.gripper_max_step!r}")
        home = _floats(self.home_joints, packing.ARM_DOF, "home_joints")
        if any(not lo <= v <= hi for v, lo, hi in zip(home, low, high, strict=True)):
            raise ConfigError(f"home_joints {home} lies outside joint_low/joint_high")
        object.__setattr__(self, "home_joints", home)
        if self.rest_joints is not None:
            rest = _floats(self.rest_joints, packing.ARM_DOF, "rest_joints")
            if any(not lo <= v <= hi for v, lo, hi in zip(rest, low, high, strict=True)):
                raise ConfigError(f"rest_joints {rest} lies outside joint_low/joint_high")
            object.__setattr__(self, "rest_joints", rest)
        for name in ("home_gripper", "rest_gripper"):
            value = getattr(self, name)
            if not (math.isfinite(value) and 0 <= value <= 1):
                raise ConfigError(f"{name} must be in [0, 1] (0 closed, 1 open), got {value!r}")
        if not (math.isfinite(self.ramp_secs) and self.ramp_secs > 0):
            raise ConfigError(f"ramp_secs must be > 0, got {self.ramp_secs!r}")
        if self.cam_width < 1 or self.cam_height < 1:
            raise ConfigError("cam_width and cam_height must be >= 1")
        object.__setattr__(self, "camera_devices", parse_cameras(self.cameras or None))

    # -- derived views -----------------------------------------------------------

    @property
    def dim(self) -> int:
        """Packed action/state length: 7 per selected arm."""
        return packing.total_dim(self.arms)

    @property
    def low(self) -> npt.NDArray[np.float64]:
        """Packed lower bounds: joint_low per arm plus 0 for each gripper."""
        return np.array((*self.joint_low, 0.0) * len(packing.sides(self.arms)), dtype=np.float64)

    @property
    def high(self) -> npt.NDArray[np.float64]:
        """Packed upper bounds: joint_high per arm plus 1 for each gripper."""
        return np.array((*self.joint_high, 1.0) * len(packing.sides(self.arms)), dtype=np.float64)

    @property
    def max_step(self) -> tuple[float | None, ...]:
        """Per-dimension safe step: joint_max_step on joints, gripper_max_step on grippers."""
        per_arm = (*((self.joint_max_step,) * packing.ARM_DOF), self.gripper_max_step)
        return per_arm * len(packing.sides(self.arms))

    @property
    def home_pose(self) -> npt.NDArray[np.float64]:
        """Packed, normalized home pose."""
        per_arm = (*self.home_joints, self.home_gripper)
        return np.array(per_arm * len(packing.sides(self.arms)), dtype=np.float64)

    @property
    def rest_pose(self) -> npt.NDArray[np.float64] | None:
        """Packed, normalized rest pose, or ``None`` to park at the first observed pose."""
        if self.rest_joints is None:
            return None
        per_arm = (*self.rest_joints, self.rest_gripper)
        return np.array(per_arm * len(packing.sides(self.arms)), dtype=np.float64)

    def replace(self, **changes: Any) -> ArxR5Config:
        """Return a copy with ``changes`` applied (re-validated)."""
        return dataclasses.replace(self, **changes)
