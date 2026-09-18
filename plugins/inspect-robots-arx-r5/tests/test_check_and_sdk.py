"""The preflight CLI on a fake rig, and the SDK path resolution errors."""

from __future__ import annotations

import pytest
from _fakes import FakeArm

from inspect_robots.errors import ConfigError, EmbodimentFault
from inspect_robots_arx_r5 import ArxR5Config
from inspect_robots_arx_r5._sdk import ENV_SDK_PATH, SdkArm, resolve_sdk_path
from inspect_robots_arx_r5.check import main


def test_check_reads_wiggles_and_restores(  # type: ignore[no-untyped-def]
    make_embodiment, arms: dict[str, FakeArm], capsys: pytest.CaptureFixture[str]
) -> None:
    emb = make_embodiment(unattended=True)
    code = main(["--seconds", "0", "--wiggle", "--gripper"], embodiment=emb)
    assert code == 0
    out = capsys.readouterr().out
    assert "connecting (no motion)" in out
    assert "moved:" in out and "restored" in out
    # The wiggle went out and came back through the real command path.
    assert len(arms["left"].joint_commands) > 2
    assert arms["left"].joints == pytest.approx([0.1, 0.2, 0.3, 0.4, 0.5, 0.6], abs=1e-9)
    assert min(arms["left"].catch_commands) == pytest.approx(0.0)
    assert arms["left"].protected and arms["left"].closed


def test_resolve_sdk_path_errors(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv(ENV_SDK_PATH, raising=False)
    with pytest.raises(ConfigError, match="sdk_path"):
        resolve_sdk_path(ArxR5Config())
    with pytest.raises(ConfigError, match="no 'bimanual' package"):
        resolve_sdk_path(ArxR5Config(sdk_path=str(tmp_path)))
    (tmp_path / "bimanual").mkdir()
    assert resolve_sdk_path(ArxR5Config(sdk_path=str(tmp_path))) == str(tmp_path)
    monkeypatch.setenv(ENV_SDK_PATH, str(tmp_path))
    assert resolve_sdk_path(ArxR5Config()) == str(tmp_path)


class _RawSdkArm:
    def __init__(self, values: list[float]):
        self.values = values
        self.calls: list[tuple[str, object]] = []

    def get_joint_positions(self) -> list[float]:
        return self.values

    def set_joint_positions(self, positions: list[float]) -> None:
        self.calls.append(("joints", positions))

    def set_catch_pos(self, position: float) -> None:
        self.calls.append(("catch", position))

    def protect_mode(self) -> None:
        self.calls.append(("protect", None))


def test_sdk_arm_adapter_validates_and_forwards() -> None:
    raw = _RawSdkArm([0.0] * 7)
    arm = SdkArm(raw, label="left")
    assert list(arm.get_joint_positions()) == [0.0] * 7
    arm.set_joint_positions([1, 2, 3, 4, 5, 6])
    arm.set_catch(2.5)
    arm.protect()
    assert raw.calls == [
        ("joints", [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]),
        ("catch", 2.5),
        ("protect", None),
    ]
    six = SdkArm(_RawSdkArm([0.0] * 6), label="left")
    assert list(six.get_joint_positions()) == [0.0] * 7  # gripper reading padded
    with pytest.raises(EmbodimentFault, match="expected 7"):
        SdkArm(_RawSdkArm([0.0] * 3), label="left").get_joint_positions()
