"""Flat joint-vector packing for one or two ARX R5 arms.

Per arm the vector is ``[j0, j1, j2, j3, j4, j5, gripper]``: the six revolute
joints in the SDK's order, gripper last. With ``arms="both"`` the full vector
is ``left`` then ``right`` (indices ``0..6`` and ``7..13``). Joints are radians;
the gripper slot is normalized on the wire (``0`` closed, ``1`` open) and
mapped to the SDK's native gripper units only at the driver boundary.

Pure NumPy, no hardware imports, so it tests anywhere.
"""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import numpy.typing as npt

from inspect_robots.spaces import StateField, StateSpec

ARM_DOF = 6
ARM_WIDTH = ARM_DOF + 1
STATE_KEY = "joint_pos"
SIDES: dict[str, tuple[str, ...]] = {
    "both": ("left", "right"),
    "left": ("left",),
    "right": ("right",),
}

Vec = npt.NDArray[np.float64]


def sides(arms: str) -> tuple[str, ...]:
    """Return the arm side names selected by ``arms`` (``both``/``left``/``right``)."""
    try:
        return SIDES[arms]
    except KeyError:
        raise ValueError(f"arms must be one of {sorted(SIDES)}, got {arms!r}") from None


def total_dim(arms: str) -> int:
    """Return the packed vector length for the selected arms."""
    return ARM_WIDTH * len(sides(arms))


def dim_labels(arms: str) -> tuple[str, ...]:
    """Return the per-dimension names, ``<side>_j0`` .. ``<side>_gripper`` per arm."""
    return tuple(
        f"{side}_{part}"
        for side in sides(arms)
        for part in (*(f"j{i}" for i in range(ARM_DOF)), "gripper")
    )


def gripper_indices(arms: str) -> tuple[int, ...]:
    """Return the packed indices of the gripper slots."""
    return tuple(ARM_DOF + ARM_WIDTH * i for i in range(len(sides(arms))))


def state_spec(arms: str) -> StateSpec:
    """The proprioception contract: one flat field, radians plus normalized grippers."""
    return StateSpec(
        fields=(StateField(key=STATE_KEY, shape=(total_dim(arms),), unit="rad+normalized"),)
    )


def validate_dim(vec: npt.ArrayLike, n: int) -> Vec:
    """Return ``vec`` as a 1-D float64 array of length ``n`` or raise ``ValueError``."""
    arr = np.asarray(vec, dtype=np.float64)
    if arr.ndim != 1 or arr.shape[0] != n:
        raise ValueError(f"expected a {n}-D vector, got shape {np.shape(vec)}")
    return arr


def split(vec: npt.ArrayLike, arms: str) -> dict[str, Vec]:
    """Split a packed vector into per-side 7-D arm vectors, keyed by side name."""
    arr = validate_dim(vec, total_dim(arms))
    return {
        side: arr[i * ARM_WIDTH : (i + 1) * ARM_WIDTH].copy() for i, side in enumerate(sides(arms))
    }


def pack(per_side: Mapping[str, npt.ArrayLike], arms: str) -> Vec:
    """Concatenate per-side 7-D arm vectors into the packed order."""
    return np.concatenate([validate_dim(per_side[side], ARM_WIDTH) for side in sides(arms)])


def norm_grippers(
    vec: npt.ArrayLike, arms: str, *, gripper_open: float, gripper_closed: float
) -> Vec:
    """Map the gripper slots from SDK-native units to ``0`` (closed) .. ``1`` (open)."""
    out = validate_dim(vec, total_dim(arms)).copy()
    span = gripper_open - gripper_closed
    for index in gripper_indices(arms):
        out[index] = (out[index] - gripper_closed) / span
    return out


def denorm_grippers(
    vec: npt.ArrayLike, arms: str, *, gripper_open: float, gripper_closed: float
) -> Vec:
    """Map normalized gripper slots back to the SDK's native units."""
    out = validate_dim(vec, total_dim(arms)).copy()
    span = gripper_open - gripper_closed
    for index in gripper_indices(arms):
        out[index] = gripper_closed + out[index] * span
    return out
