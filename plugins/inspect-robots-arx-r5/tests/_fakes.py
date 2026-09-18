"""Hardware stand-ins shared by the plugin tests."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import numpy.typing as npt


class FakeArm:
    """An arm that reports exactly what it was last commanded (plus a start pose)."""

    def __init__(self, start: Sequence[float] | None = None):
        self.joints = list(start[:6]) if start else [0.1, 0.2, 0.3, 0.4, 0.5, 0.6]
        self.gripper = float(start[6]) if start else 2.0
        self.joint_commands: list[list[float]] = []
        self.catch_commands: list[float] = []
        self.protected = False
        self.closed = False

    def get_joint_positions(self) -> Sequence[float]:
        return [*self.joints, self.gripper]

    def set_joint_positions(self, positions: Sequence[float]) -> None:
        assert len(positions) == 6
        self.joints = [float(v) for v in positions]
        self.joint_commands.append(list(self.joints))

    def set_catch(self, position: float) -> None:
        self.gripper = float(position)
        self.catch_commands.append(self.gripper)

    def protect(self) -> None:
        self.protected = True

    def close(self) -> None:
        self.closed = True


class FakeCameras:
    def __init__(self, names: Sequence[str], height: int = 48, width: int = 64):
        self.names = list(names)
        self.height = height
        self.width = width
        self.calls = 0
        self.closed = False

    def __call__(self) -> dict[str, npt.NDArray[np.uint8]]:
        self.calls += 1
        return {
            name: np.full((self.height, self.width, 3), 7 * (i + 1), dtype=np.uint8)
            for i, name in enumerate(self.names)
        }

    def close(self) -> None:
        self.closed = True


class FakeSession:
    def __init__(self) -> None:
        self.gates: list[str] = []
        self.lines: list[str] = []
        self.statuses: list[str | None] = []

    def status(self, line: str | None) -> None:
        self.statuses.append(line)

    def write_line(self, text: str) -> None:
        self.lines.append(text)

    def gate(self, prompt: str, *, hint: str | None = None) -> None:
        self.gates.append(prompt)
