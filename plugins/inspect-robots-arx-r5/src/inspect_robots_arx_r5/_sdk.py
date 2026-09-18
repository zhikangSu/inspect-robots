"""Thin driver layer over the official ARX R5 Python SDK.

The SDK (``github.com/ARXroboticsX/R5``, ``py/ARX_R5_python``) is a pybind
module ``arx_r5_python`` plus a ``bimanual`` wrapper package that is built on
the rig with ``./build.sh``. It is not pip-installable, so this module injects
``sdk_path`` into ``sys.path`` and imports it lazily on first connect. Tests
inject a fake driver through ``driver_factory`` and never import it.

SDK facts this layer relies on (from the SDK sources):

* ``bimanual.SingleArm({"can_port": "can1", "type": 0})`` opens one arm.
* ``get_joint_positions()`` returns 7 values: six joints then the gripper.
* ``set_joint_positions(list_of_6)`` commands joint targets and switches the
  arm to joint-position mode (SDK status ``5``).
* ``set_catch_pos(float)`` commands the gripper in the SDK's native units.
* ``go_home()`` (status ``1``) and ``protect_mode()`` (status ``2``) exist.
"""

from __future__ import annotations

import importlib
import os
import sys
from collections.abc import Callable, Sequence
from typing import Any, Protocol

from inspect_robots.errors import ConfigError, EmbodimentFault
from inspect_robots_arx_r5.config import ArxR5Config
from inspect_robots_arx_r5.packing import ARM_DOF, ARM_WIDTH

ENV_SDK_PATH = "ARX_R5_PYTHON"


class ArmDriver(Protocol):
    """One arm as the embodiment sees it. Real and fake drivers implement this."""

    def get_joint_positions(self) -> Sequence[float]:
        """Return seven values: six joint angles (rad) then the native gripper reading."""
        ...

    def set_joint_positions(self, positions: Sequence[float]) -> None:
        """Command six joint targets (rad) in joint-position mode."""
        ...

    def set_catch(self, position: float) -> None:
        """Command the gripper in the SDK's native units."""
        ...

    def protect(self) -> None:
        """Put the arm into the SDK's protective (safe) mode."""
        ...

    def close(self) -> None:
        """Release the CAN handle."""
        ...


DriverFactory = Callable[[ArxR5Config], dict[str, ArmDriver]]


def resolve_sdk_path(cfg: ArxR5Config) -> str:
    """Return the directory holding the SDK's ``bimanual`` package, or raise ``ConfigError``."""
    path = cfg.sdk_path or os.environ.get(ENV_SDK_PATH)
    if not path:
        raise ConfigError(
            "arx_r5 needs the ARX R5 Python SDK: set sdk_path (-E sdk_path=...) or "
            f"${ENV_SDK_PATH} to the R5 repo's py/ARX_R5_python directory after ./build.sh"
        )
    if not os.path.isdir(os.path.join(path, "bimanual")):
        raise ConfigError(
            f"sdk_path {path!r} has no 'bimanual' package; expected .../R5/py/ARX_R5_python"
        )
    return path


def load_sdk(cfg: ArxR5Config) -> Any:  # pragma: no cover - real SDK import
    """Import the SDK's ``bimanual`` package from ``sdk_path``."""
    path = resolve_sdk_path(cfg)
    if path not in sys.path:
        sys.path.insert(0, path)
    try:
        return importlib.import_module("bimanual")
    except Exception as exc:
        raise EmbodimentFault(
            f"failed to import the ARX R5 SDK from {path!r}: {exc!r}\n"
            "fix: cd py/ARX_R5_python && ./build.sh && source ./setup.sh, and run from a "
            "shell where LD_LIBRARY_PATH includes the SDK's api/ directories"
        ) from exc


class SdkArm:
    """Adapter from the SDK's ``SingleArm`` object to :class:`ArmDriver`."""

    def __init__(self, single_arm: Any, *, label: str):
        self._arm = single_arm
        self._label = label

    def get_joint_positions(self) -> Sequence[float]:
        """Read the SDK's seven-value joint vector, validating its length."""
        values = list(self._arm.get_joint_positions())
        if len(values) not in (ARM_DOF, ARM_WIDTH):
            raise EmbodimentFault(
                f"{self._label}: SDK returned {len(values)} joint values, expected {ARM_WIDTH}"
            )
        if len(values) == ARM_DOF:
            # Some builds omit the gripper reading; report it as unknown (closed).
            values.append(0.0)
        return values

    def set_joint_positions(self, positions: Sequence[float]) -> None:
        """Forward six joint targets to the SDK."""
        self._arm.set_joint_positions([float(v) for v in positions])

    def set_catch(self, position: float) -> None:
        """Forward the native gripper target to the SDK."""
        self._arm.set_catch_pos(float(position))

    def protect(self) -> None:
        """Switch the arm into the SDK's protective mode."""
        self._arm.protect_mode()

    def close(self) -> None:
        """Drop the SDK object; its destructor releases the CAN handle."""
        self._arm = None


def default_driver_factory(cfg: ArxR5Config) -> dict[str, ArmDriver]:  # pragma: no cover
    """Open the configured arms through the real SDK, keyed by side name."""
    sdk = load_sdk(cfg)
    drivers: dict[str, ArmDriver] = {}
    channels = {"left": cfg.left_can, "right": cfg.right_can}
    for side in ("left", "right"):
        if cfg.arms not in ("both", side):
            continue
        try:
            arm = sdk.SingleArm({"can_port": channels[side], "type": cfg.arm_type})
        except Exception as exc:
            for opened in drivers.values():
                opened.close()
            raise EmbodimentFault(
                f"could not open the {side} R5 arm on {channels[side]}: {exc!r}\n"
                "fix: bring the CAN interface up with the SDK's arx_canN.sh script and "
                "check the USB2CAN link"
            ) from exc
        drivers[side] = SdkArm(arm, label=f"{side} arm ({channels[side]})")
    return drivers
