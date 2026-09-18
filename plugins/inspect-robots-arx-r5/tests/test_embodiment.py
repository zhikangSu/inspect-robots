"""Hardware-free tests: declarations, conformance, lifecycle, and packing."""

from __future__ import annotations

import numpy as np
import pytest
from _fakes import FakeArm, FakeSession

from inspect_robots import Action, Embodiment, Scene
from inspect_robots.conformance import (
    assert_embodiment_conformant,
    assert_guardrail_contribution_conformant,
    device_slots,
    number_slots,
    option_slots,
)
from inspect_robots.errors import ConfigError, EmbodimentFault
from inspect_robots.registry import resolve
from inspect_robots_arx_r5 import ArxR5Config, ArxR5Embodiment, arx_r5_embodiment, packing

SCENE = Scene(id="s0", instruction="pick up the cube")


def test_info_declares_bimanual_joint_pos(make_embodiment) -> None:  # type: ignore[no-untyped-def]
    emb = make_embodiment()
    assert isinstance(emb, Embodiment)
    info = emb.info
    assert info.name == "arx_r5"
    assert info.action_space.dim == 14
    sem = info.action_space.semantics
    assert sem is not None and sem.control_mode == "joint_pos"
    assert sem.dim_labels == packing.dim_labels("both")
    assert sem.dim_labels[6] == "left_gripper" and sem.dim_labels[13] == "right_gripper"
    assert info.control_hz == 10.0
    assert "self_paced" in info.capabilities
    assert tuple(c.name for c in info.observation_space.cameras) == ("top", "wrist")
    assert info.docs is not None and "gripper: 0 is fully closed" in info.docs
    assert_embodiment_conformant(info)
    assert_guardrail_contribution_conformant(emb, info.action_space)


def test_single_arm_is_seven_dimensional(make_embodiment) -> None:  # type: ignore[no-untyped-def]
    emb = make_embodiment(arms="left")
    assert emb.info.name == "arx_r5:left"
    assert emb.info.action_space.dim == 7
    assert emb.info.docs is not None and "Only the left arm" in emb.info.docs
    assert_embodiment_conformant(emb.info)


def test_registry_factory_and_wizard_slots() -> None:
    emb = resolve("embodiment", "arx_r5", arms="right", right_can="can3", unattended=True)
    assert isinstance(emb, ArxR5Embodiment)
    assert emb.config.arms == "right"
    assert [s.arg for s in device_slots(arx_r5_embodiment)] == ["left_can", "right_can"]
    assert {s.arg for s in option_slots(arx_r5_embodiment)} == {"auto_start", "park_before_grade"}
    assert {s.arg for s in number_slots(arx_r5_embodiment)} == {"ramp_secs", "joint_max_step"}


def test_config_parses_cli_strings_and_rejects_bad_values() -> None:
    cfg = ArxR5Config.from_kwargs(
        joint_low="-1,-1,-1,-1,-1,-1", joint_high="1,1,1,1,1,1", cameras="cam:/dev/video9"
    )
    assert cfg.joint_low == (-1.0,) * 6
    assert cfg.camera_devices == {"cam": "/dev/video9"}
    assert cfg.low.tolist() == [-1.0] * 6 + [0.0] + [-1.0] * 6 + [0.0]
    with pytest.raises(ConfigError, match="unknown arx_r5 option"):
        ArxR5Config.from_kwargs(left_channel="can1")
    with pytest.raises(ConfigError, match="joint_low must be <"):
        ArxR5Config(joint_low=(1.0,) * 6, joint_high=(0.5,) * 6)
    with pytest.raises(ConfigError, match="must differ"):
        ArxR5Config(gripper_open=1.0, gripper_closed=1.0)
    with pytest.raises(ConfigError, match="left_can and right_can"):
        ArxR5Config(left_can="can1", right_can="can1")
    with pytest.raises(ConfigError, match="cameras entry"):
        ArxR5Config(cameras="top")
    with pytest.raises(ConfigError, match="home_joints"):
        ArxR5Config(joint_low=(0.0,) * 6, joint_high=(1.0,) * 6, home_joints=(2.0,) * 6)


def test_reset_gates_homes_and_observes(make_embodiment, arms: dict[str, FakeArm]) -> None:  # type: ignore[no-untyped-def]
    emb = make_embodiment()
    session = FakeSession()
    emb.connect_operator_session(session)
    obs = emb.reset(SCENE)
    assert session.gates == [
        "Arms will move to the home pose - stand clear, then press Enter...",
        "Position the scene, then press Enter to start...",
    ]
    # ramp_secs=0.2 at 10 Hz is 2 waypoints per arm, ending exactly at home.
    assert len(arms["left"].joint_commands) == 2
    assert arms["left"].joints == [0.0] * 6
    assert arms["left"].gripper == pytest.approx(4.0)  # home_gripper 1.0 -> native open
    assert obs.instruction == "pick up the cube"
    state = obs.state["joint_pos"]
    assert state.shape == (14,)
    assert state[6] == pytest.approx(1.0) and state[13] == pytest.approx(1.0)
    assert obs.images["top"].shape == (48, 64, 3)
    # Second reset on the same connection: no stand-clear gate again.
    emb.reset(SCENE)
    assert (
        session.gates.count("Arms will move to the home pose - stand clear, then press Enter...")
        == 1
    )


def test_step_clamps_denormalizes_and_paces(make_embodiment, arms: dict[str, FakeArm]) -> None:  # type: ignore[no-untyped-def]
    emb = make_embodiment(unattended=True, joint_low=(-1.0,) * 6, joint_high=(1.0,) * 6)
    emb.reset(SCENE)
    cmd = np.zeros(14)
    cmd[0] = 5.0  # clamped to 1.0
    cmd[6] = 0.5  # half open -> native 2.0
    cmd[7 + 2] = -0.25
    cmd[13] = 0.0  # closed -> native 0.0
    result = emb.step(Action(data=cmd))
    assert arms["left"].joint_commands[-1][0] == pytest.approx(1.0)
    assert arms["left"].catch_commands[-1] == pytest.approx(2.0)
    assert arms["right"].joint_commands[-1][2] == pytest.approx(-0.25)
    assert arms["right"].catch_commands[-1] == pytest.approx(0.0)
    assert result.terminated is False
    assert result.observation.state["joint_pos"][6] == pytest.approx(0.5)
    assert emb.num_steps == 1


def test_step_before_reset_faults(make_embodiment) -> None:  # type: ignore[no-untyped-def]
    emb = make_embodiment(unattended=True)
    with pytest.raises(EmbodimentFault, match="before reset"):
        emb.step(Action(data=np.zeros(14)))


def test_observe_parked_and_close_return_to_initial_pose(  # type: ignore[no-untyped-def]
    make_embodiment, arms: dict[str, FakeArm]
) -> None:
    emb = make_embodiment(unattended=True)
    assert emb.observe_parked() is None  # not connected yet
    emb.reset(SCENE)
    emb.step(Action(data=np.full(14, 0.3)))
    parked = emb.observe_parked()
    assert parked is not None and parked.instruction is None
    # rest_joints is None, so the park target is the pose captured at first connect.
    assert arms["left"].joints == pytest.approx([0.1, 0.2, 0.3, 0.4, 0.5, 0.6])
    assert arms["right"].joints == pytest.approx([0.0] * 6)
    emb.close()
    assert arms["left"].protected and arms["left"].closed
    assert arms["right"].protected and arms["right"].closed
    reader = emb._camera_reader
    assert reader is not None and getattr(reader, "closed", False)
    emb.close()  # idempotent


def test_park_before_grade_off_declines(make_embodiment) -> None:  # type: ignore[no-untyped-def]
    emb = make_embodiment(unattended=True, park_before_grade=False)
    emb.reset(SCENE)
    assert emb.observe_parked() is None


def test_explicit_rest_pose_is_used(make_embodiment, arms: dict[str, FakeArm]) -> None:  # type: ignore[no-untyped-def]
    emb = make_embodiment(unattended=True, rest_joints=(0.5,) * 6, rest_gripper=0.0)
    emb.reset(SCENE)
    emb.close()
    assert arms["left"].joints == pytest.approx([0.5] * 6)
    assert arms["left"].gripper == pytest.approx(0.0)


def test_non_finite_reading_faults(make_embodiment, arms: dict[str, FakeArm]) -> None:  # type: ignore[no-untyped-def]
    emb = make_embodiment(unattended=True)
    arms["left"].joints[2] = float("nan")
    with pytest.raises(EmbodimentFault, match="non-finite"):
        emb.reset(SCENE)


def test_packing_round_trip() -> None:
    vec = np.arange(14, dtype=np.float64)
    parts = packing.split(vec, "both")
    assert parts["left"].tolist() == list(range(7))
    assert packing.pack(parts, "both").tolist() == vec.tolist()
    normed = packing.norm_grippers(vec, "both", gripper_open=4.0, gripper_closed=0.0)
    assert normed[6] == pytest.approx(1.5) and normed[13] == pytest.approx(3.25)
    back = packing.denorm_grippers(normed, "both", gripper_open=4.0, gripper_closed=0.0)
    assert back.tolist() == vec.tolist()
    with pytest.raises(ValueError, match="arms must be one of"):
        packing.sides("middle")
