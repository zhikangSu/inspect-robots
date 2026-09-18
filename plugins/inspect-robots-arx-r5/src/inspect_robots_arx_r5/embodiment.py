"""The ``arx_r5`` embodiment: one or two ARX R5 arms in joint-position control.

Lifecycle as the framework drives it:

* ``reset()`` opens the arms on first use, remembers where the operator left
  them, asks the operator to stand clear (unless unattended/auto_start),
  ramps to the home pose, waits for the scene to be set, and returns the
  first observation.
* ``step()`` clamps the packed action to the declared bounds, sends each
  arm's six joints and gripper, paces to ``control_hz`` (the framework
  itself never sleeps; this adapter declares ``self_paced``), and observes.
* ``observe_parked()`` ramps to the rest pose before grading so cameras see
  the table unobstructed.
* ``close()`` ramps back to rest and puts every arm in protective mode.

The adapter contains no collision model; safety relies on the framework's
bounds clamp and per-step delta limit (both derived from this config) plus
the operator's e-stop. Keep ``max_speed_frac`` low on first runs.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable, Iterator, Mapping
from typing import Any, ClassVar, Protocol

import numpy as np
import numpy.typing as npt

from inspect_robots.approver import GuardrailContribution
from inspect_robots.conformance import DeviceSlot, NumberSlot, OptionSlot
from inspect_robots.embodiment import SELF_PACED, EmbodimentInfo
from inspect_robots.errors import EmbodimentFault
from inspect_robots.scene import Scene
from inspect_robots.spaces import ActionSemantics, Box, CameraSpec, ObservationSpace
from inspect_robots.types import Action, Observation, StepResult
from inspect_robots_arx_r5 import packing
from inspect_robots_arx_r5._cameras import CameraReader, ImageMap, OpenCVCameraReader
from inspect_robots_arx_r5._sdk import ArmDriver, DriverFactory, default_driver_factory
from inspect_robots_arx_r5.config import ArxR5Config

Vec = npt.NDArray[np.float64]

_DOCS = """ARX R5 arm(s) in joint-position control. Each selected arm contributes seven
dimensions prefixed left_ or right_: six revolute joints j0..j5 in the SDK's
order, then a parallel gripper. Joint values are radians as read back from
the arm. At the SDK's zero pose the arm is folded and the tool frame
coincides with the base frame. Joint axes, from the SDK's URDF, with the
positive direction following the right-hand rule about the axis:
- j0: base rotation about the vertical axis.
- j1: shoulder, about the base's y axis.
- j2: elbow, about the same y axis.
- j3: wrist pitch, about y.
- j4: wrist rotation about the vertical axis of the wrist.
- j5: tool roll about the tool's pointing axis.
- gripper: 0 is fully closed, 1 is fully open (about 80 mm between jaws).
A serial arm has singular poses near the edge of its workspace; keep motions
modest and re-check the observation after each one. The arm stops by itself
if a joint reaches its hardware limit."""


class OperatorSessionLike(Protocol):
    """The slice of the framework's operator session this adapter uses."""

    def status(self, line: str | None) -> None:
        """Show or clear an in-place status line."""
        ...

    def write_line(self, text: str) -> None:
        """Print a scrollback line above the status."""
        ...

    def gate(self, prompt: str, *, hint: str | None = None) -> None:
        """Block until the operator confirms readiness."""
        ...


class TaskEnvelopeLike(Protocol):
    """Structural mirror of the framework's ``TaskEnvelope``."""

    @property
    def name(self) -> str:
        """Task name."""
        ...

    @property
    def max_steps(self) -> int:
        """Resolved step budget."""
        ...


def ramp_waypoints(start: Vec, target: Vec, n: int) -> Iterator[Vec]:
    """Yield ``n`` linear waypoints after ``start``, ending exactly at ``target``."""
    for index in range(1, n + 1):
        alpha = index / n
        yield (1.0 - alpha) * start + alpha * target


def _stderr_status(line: str | None) -> None:  # pragma: no cover - real TTY output
    if line is not None:
        print(f"[arx_r5] {line}", file=sys.stderr, flush=True)


class ArxR5Embodiment:
    """Inspect Robots embodiment for ARX R5 arms over the official Python SDK."""

    RUNTIME_REQUIREMENTS: ClassVar[Mapping[str, str]] = {
        "cv2": 'uv pip install "inspect-robots-arx-r5[cameras]"',
    }
    DEVICE_SLOTS: ClassVar[tuple[DeviceSlot, ...]] = (
        DeviceSlot(arg="left_can", kind="can", label="left arm CAN interface", group="arms"),
        DeviceSlot(arg="right_can", kind="can", label="right arm CAN interface", group="arms"),
    )
    OPTION_SLOTS: ClassVar[tuple[OptionSlot, ...]] = (
        OptionSlot(
            arg="auto_start", label="Skip the stand-clear and scene-ready prompts", default=False
        ),
        OptionSlot(
            arg="park_before_grade",
            label="Park the arms at rest before the grader looks at the final frames",
            default=True,
        ),
    )
    NUMBER_SLOTS: ClassVar[tuple[NumberSlot, ...]] = (
        NumberSlot(
            arg="ramp_secs",
            label="Seconds for homing/parking ramps",
            default=3.0,
            minimum=0.5,
            maximum=20.0,
        ),
        NumberSlot(
            arg="joint_max_step",
            label="Largest per-step joint change the delta limiter allows (rad)",
            default=0.1,
            minimum=0.005,
            maximum=0.5,
        ),
    )

    def __init__(
        self,
        config: ArxR5Config | None = None,
        *,
        driver_factory: DriverFactory | None = None,
        camera_reader: CameraReader | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        **kwargs: Any,
    ):
        if config is None:
            config = ArxR5Config.from_kwargs(**kwargs)
        elif kwargs:
            config = config.replace(**kwargs)
        self._cfg = config
        self._driver_factory = driver_factory or default_driver_factory
        if camera_reader is not None:
            self._camera_reader: CameraReader | None = camera_reader
        elif config.camera_devices:
            self._camera_reader = OpenCVCameraReader(
                config.camera_devices, width=config.cam_width, height=config.cam_height
            )
        else:
            self._camera_reader = None
        self._clock = clock
        self._sleep = sleep
        self._drivers: dict[str, ArmDriver] | None = None
        self._init_pose: Vec | None = None
        self._session: OperatorSessionLike | None = None
        self._status: Callable[[str | None], None] = _stderr_status
        self._bound_max_steps: int | None = None
        self._instruction: str | None = None
        self._t_last = 0.0
        self.num_steps = 0
        self.info = self._build_info()

    # -- declarations -----------------------------------------------------------

    def _build_info(self) -> EmbodimentInfo:
        cfg = self._cfg
        semantics = ActionSemantics(
            control_mode="joint_pos",
            rotation_repr="none",
            gripper="continuous",
            frame="base",
            dim_labels=packing.dim_labels(cfg.arms),
            max_step=cfg.max_step,
        )
        cameras = tuple(
            CameraSpec(name=name, height=cfg.cam_height, width=cfg.cam_width, channels=3)
            for name in cfg.camera_devices
        )
        spec = packing.state_spec(cfg.arms)
        docs = _DOCS
        if cfg.arms != "both":
            docs += f"\nOnly the {cfg.arms} arm is connected in this run."
        if cfg.docs_extra.strip():
            docs += "\n\n" + cfg.docs_extra.strip()
        return EmbodimentInfo(
            name="arx_r5" if cfg.arms == "both" else f"arx_r5:{cfg.arms}",
            action_space=Box(shape=(cfg.dim,), low=cfg.low, high=cfg.high, semantics=semantics),
            observation_space=ObservationSpace(cameras=cameras, state_keys=spec.keys, state=spec),
            control_hz=cfg.control_hz,
            is_simulated=False,
            capabilities=frozenset({SELF_PACED}),
            docs=docs,
        )

    @property
    def config(self) -> ArxR5Config:
        """The validated configuration this embodiment runs with."""
        return self._cfg

    def contribute_guardrails(self, action_space: Box) -> GuardrailContribution:
        """No rig-specific approver yet; make that visible in the guardrails banner."""
        return GuardrailContribution(
            warnings=(
                "arx_r5 ships no collision guardrail: only the bounds clamp and the "
                "per-step delta limit protect the arm; keep -P max_speed_frac low and "
                "the e-stop in reach",
            )
        )

    def connect_operator_session(self, session: OperatorSessionLike) -> None:
        """Adopt the framework console for prompts and status; never touch stdin ourselves."""
        self._session = session
        self._status = session.status

    def bind_task(self, envelope: TaskEnvelopeLike) -> None:
        """Remember the step budget so the running banner can show a time estimate."""
        self._bound_max_steps = int(envelope.max_steps)

    # -- lifecycle --------------------------------------------------------------

    def reset(self, scene: Scene, *, seed: int | None = None) -> Observation:
        """Connect on first use, home the arms behind operator gates, and observe."""
        cfg = self._cfg
        self._instruction = scene.instruction
        first_connect = self._drivers is None
        if self._drivers is None:
            self._drivers = self._driver_factory(cfg)
            missing = [side for side in packing.sides(cfg.arms) if side not in self._drivers]
            if missing:
                raise EmbodimentFault(f"driver factory did not open arm(s): {missing}")
        if self._init_pose is None:
            self._init_pose = self._read_state()
        if not cfg.unattended and first_connect:
            notice = "Arms will move to the home pose - stand clear"
            if cfg.auto_start:
                self._write_line(f"auto_start: {notice}.")
            elif self._session is not None:
                self._session.gate(
                    f"{notice}, then press Enter...",
                    hint="Set -E unattended=true or -E auto_start=true to skip this prompt.",
                )
            else:
                self._input_gate(f"{notice}, then press Enter...")
        self._status("homing: ramping arm(s) to the start pose")
        try:
            self._ramp_to(cfg.home_pose)
        finally:
            self._status(None)
        if not cfg.unattended and not cfg.auto_start:
            if self._session is not None:
                self._session.gate(
                    "Position the scene, then press Enter to start...",
                    hint="Set -E auto_start=true to skip this prompt.",
                )
            else:
                self._input_gate("Position the scene, then press Enter to start...")
        if not cfg.unattended:
            limit = ""
            if self._bound_max_steps is not None:
                limit = f" Max ~{self._bound_max_steps / cfg.control_hz:.0f}s."
            self._status(f"Running.{limit}")
        self.num_steps = 0
        self._t_last = self._clock()
        return self._observe(scene.instruction)

    def step(self, action: Action) -> StepResult:
        """Clamp, command every arm, pace to the control rate, then observe."""
        self._require_drivers()
        cmd = packing.validate_dim(action.data, self._cfg.dim)
        self._send(cmd)
        self._pace()
        self.num_steps += 1
        return StepResult(observation=self._observe(self._instruction), terminated=False)

    def observe_parked(self) -> Observation | None:
        """Ramp to rest before grading so the cameras see the scene unobstructed."""
        if not self._cfg.park_before_grade or self._drivers is None or self._init_pose is None:
            return None
        target = self._cfg.rest_pose if self._cfg.rest_pose is not None else self._init_pose
        self._status("parking for grading")
        try:
            self._ramp_to(target)
        finally:
            self._status(None)
        observation = self._observe(None)
        return Observation(images=observation.images, state=observation.state)

    def close(self) -> None:
        """Park at rest, switch every arm to protective mode, and release cameras."""
        self._bound_max_steps = None
        try:
            if self._drivers is None:
                return
            try:
                if self._init_pose is not None:
                    target = (
                        self._cfg.rest_pose if self._cfg.rest_pose is not None else self._init_pose
                    )
                    self._status("parking: ramping arm(s) to rest")
                    try:
                        self._ramp_to(target)
                    finally:
                        self._status(None)
            finally:
                errors: list[Exception] = []
                for driver in self._drivers.values():
                    for call in (driver.protect, driver.close):
                        try:
                            call()
                        except Exception as exc:  # keep releasing the other arm
                            errors.append(exc)
                self._drivers = None
                self._init_pose = None
                if errors:
                    raise EmbodimentFault(f"error(s) while releasing arms: {errors!r}")
        finally:
            reader = self._camera_reader
            release = getattr(reader, "close", None)
            if callable(release):
                release()

    # -- internals --------------------------------------------------------------

    def _require_drivers(self) -> dict[str, ArmDriver]:
        if self._drivers is None:
            raise EmbodimentFault("arx_r5: step() before reset(); the arms are not connected")
        return self._drivers

    def _write_line(self, text: str) -> None:
        if self._session is not None:
            self._session.write_line(text)
        else:  # pragma: no cover - real TTY output
            print(f"[arx_r5] {text}", file=sys.stderr, flush=True)

    def _input_gate(self, prompt: str) -> None:  # pragma: no cover - real stdin
        if not sys.stdin or not sys.stdin.isatty():
            raise EmbodimentFault(
                "arx_r5 needs an interactive terminal for the operator prompts\n"
                "fix: run from a TTY, or pass -E unattended=true for headless runs"
            )
        try:
            input(f"[arx_r5] {prompt} ")
        except EOFError as exc:
            raise EmbodimentFault("operator prompt got EOF; aborting before any motion") from exc

    def _read_state(self) -> Vec:
        """Read every arm and return the packed, gripper-normalized state vector."""
        drivers = self._require_drivers()
        per_side: dict[str, Vec] = {}
        for side in packing.sides(self._cfg.arms):
            raw = np.asarray(drivers[side].get_joint_positions(), dtype=np.float64)
            per_side[side] = packing.validate_dim(raw, packing.ARM_WIDTH)
        packed = packing.pack(per_side, self._cfg.arms)
        if not bool(np.all(np.isfinite(packed))):
            raise EmbodimentFault(f"arx_r5: non-finite joint reading {packed.tolist()}")
        return packing.norm_grippers(
            packed,
            self._cfg.arms,
            gripper_open=self._cfg.gripper_open,
            gripper_closed=self._cfg.gripper_closed,
        )

    def _send(self, cmd: Vec) -> Vec:
        """Clamp to the declared box, de-normalize grippers, and command each arm."""
        drivers = self._require_drivers()
        clamped = np.clip(cmd, self._cfg.low, self._cfg.high)
        physical = packing.denorm_grippers(
            clamped,
            self._cfg.arms,
            gripper_open=self._cfg.gripper_open,
            gripper_closed=self._cfg.gripper_closed,
        )
        for side, vec in packing.split(physical, self._cfg.arms).items():
            drivers[side].set_joint_positions(vec[: packing.ARM_DOF].tolist())
            drivers[side].set_catch(float(vec[packing.ARM_DOF]))
        return clamped

    def _ramp_to(self, target: Vec) -> Vec:
        """Linearly ramp from the current pose to ``target`` over ``ramp_secs``."""
        start = self._read_state()
        hz = self._cfg.control_hz
        n = max(1, round(self._cfg.ramp_secs * hz))
        sent = start
        for waypoint in ramp_waypoints(start, target, n):
            sent = self._send(waypoint)
            self._sleep(1.0 / hz)
        self._t_last = self._clock()
        return sent

    def _pace(self) -> None:
        elapsed = self._clock() - self._t_last
        self._sleep(max(0.0, 1.0 / self._cfg.control_hz - elapsed))
        self._t_last = self._clock()

    def _observe(self, instruction: str | None) -> Observation:
        state = self._read_state()
        images: ImageMap = {}
        if self._camera_reader is not None:
            images = dict(self._camera_reader())
            expected = (self._cfg.cam_height, self._cfg.cam_width, 3)
            for name in self._cfg.camera_devices:
                img = images.get(name)
                if img is None:
                    raise EmbodimentFault(f"camera {name!r} returned no frame")
                if img.shape != expected:
                    raise EmbodimentFault(
                        f"camera {name!r} returned shape {img.shape}, expected {expected}"
                    )
        return Observation(images=images, state={packing.STATE_KEY: state}, instruction=instruction)
