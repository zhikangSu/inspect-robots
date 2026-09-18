from __future__ import annotations

import pytest
from _fakes import FakeArm, FakeCameras

from inspect_robots_arx_r5 import ArxR5Config, ArxR5Embodiment
from inspect_robots_arx_r5._sdk import ArmDriver


@pytest.fixture
def arms() -> dict[str, FakeArm]:
    return {"left": FakeArm(), "right": FakeArm([0.0] * 6 + [4.0])}


@pytest.fixture
def make_embodiment(arms: dict[str, FakeArm]):  # type: ignore[no-untyped-def]
    def factory(**overrides: object) -> ArxR5Embodiment:
        cfg = ArxR5Config(
            cameras="top:/dev/video0,wrist:/dev/video2",
            cam_width=64,
            cam_height=48,
            ramp_secs=0.2,
            control_hz=10.0,
            gripper_open=4.0,
            gripper_closed=0.0,
        )
        cfg = cfg.replace(**overrides)
        selected = {side: arms[side] for side in ("left", "right") if cfg.arms in ("both", side)}

        def driver_factory(_: ArxR5Config) -> dict[str, ArmDriver]:
            return dict(selected)

        clock = [0.0]

        def now() -> float:
            return clock[0]

        def sleep(seconds: float) -> None:
            clock[0] += seconds

        return ArxR5Embodiment(
            cfg,
            driver_factory=driver_factory,
            camera_reader=FakeCameras(list(cfg.camera_devices), cfg.cam_height, cfg.cam_width),
            clock=now,
            sleep=sleep,
        )

    return factory
