# inspect-robots-arx-r5

An embodiment adapter for [ARX (方舟无限) R5](https://github.com/ARXroboticsX/R5)
arms, single or dual, driven through the official `ARX_R5_python` SDK over
CAN. Registers the `arx_r5` embodiment so any Inspect Robots policy (the `agent`
LLM policy, XPolicyLab-served VLAs, scripted policies) can run on the arm.

> [!WARNING]
> This adapter sends commands to physical hardware and ships no collision
> model. Keep a tested emergency stop within reach, verify the conventions
> below with `inspect-robots-arx-r5-check` before the first eval, and start
> with a low policy speed (`-P max_speed_frac=0.05`).

## Install

On the rig host, build the official SDK first (the SDK is not on PyPI):

```bash
git clone https://github.com/ARXroboticsX/R5
cd R5/py/ARX_R5_python && ./build.sh      # needs pybind11 + can-utils, see the SDK PDF
source ./setup.sh                          # exports LD_LIBRARY_PATH; repeat per shell
export ARX_R5_PYTHON=$PWD                  # or pass -E sdk_path=$PWD
```

Bring the CAN interfaces up with the SDK's scripts (`ARX_CAN/arx_can1.sh` for
the left arm, `arx_can3.sh` for the right, per ARX's dual-arm convention),
then install this plugin into the same environment:

```bash
uv pip install inspect-robots "inspect-robots-arx-r5[cameras]"
inspect-robots list embodiments            # -> arx_r5
```

## Verify before moving

```bash
inspect-robots-arx-r5-check --arms both --left-can can1 --right-can can3
```

This connects without moving and prints each arm's readings for a few
seconds. Move joints by hand to confirm the index order, then open and close
the gripper by hand and note the two readings: pass them as
`-E gripper_open=... -E gripper_closed=...` (defaults `4.0` / `0.0` are a
guess and must be checked). Add `--wiggle` to move the last joint by
0.05 rad and back, and `--gripper` to close and reopen the gripper, both
through the exact command path the eval uses.

## Run

```bash
inspect-robots "pick up the red cube" --policy agent --embodiment arx_r5 \
    -P model=anthropic/claude-sonnet-5 -P max_speed_frac=0.05 \
    -E cameras=top:/dev/video0,wrist:/dev/video2 \
    -E joint_low=-2.6,-0.1,-0.1,-1.5,-1.5,-2.0 \
    -E joint_high=2.6,3.0,3.0,1.5,1.5,2.0
```

Or write the settings once with `inspect-robots setup` (the wizard offers the
CAN slots and behavior toggles this plugin declares) and run
`inspect-robots "pick up the red cube"`.

## Configuration

Pass as `-E key=value`, as `[embodiment.args]` in `config.ini`, or as
`ArxR5Config(...)` in Python. Tuples are comma-separated on the CLI.

| Key | Default | Meaning |
|---|---|---|
| `arms` | `both` | `both` (14-D, left then right), `left`, or `right` (7-D) |
| `left_can` / `right_can` | `can1` / `can3` | SocketCAN interfaces |
| `sdk_path` | `$ARX_R5_PYTHON` | Directory containing the SDK's `bimanual` package |
| `arm_type` | `0` | Forwarded to the SDK's `SingleArm({"type": ...})` |
| `control_hz` | `20` | Command rate; the adapter paces itself (`self_paced`) |
| `joint_low` / `joint_high` | ±π ×6 | Per-joint bounds in radians, same for both arms. Tighten to your rig |
| `joint_max_step` | `0.1` | Per-step joint change the default delta limiter allows (rad) |
| `gripper_closed` / `gripper_open` | `0.0` / `4.0` | SDK-native gripper readings at the extremes. Verify |
| `gripper_max_step` | `0.2` | Per-step gripper change in normalized units |
| `home_joints` / `home_gripper` | zeros / `1.0` | Trial start pose (gripper normalized, 1 = open) |
| `rest_joints` / `rest_gripper` | `None` / `1.0` | Park pose for grading and close; `None` returns to where the arm was at first connect |
| `ramp_secs` | `3.0` | Duration of homing and parking ramps |
| `cameras` | none | `name:/dev/videoN,...`; frames are `cam_height`×`cam_width` RGB |
| `cam_width` / `cam_height` | `640` / `480` | Delivered frame size |
| `unattended` | `false` | Skip every operator prompt (headless) |
| `auto_start` | `false` | Skip only the stand-clear and scene-ready prompts |
| `park_before_grade` | `true` | Ramp to rest before the grader captures final frames |
| `docs_extra` | `""` | Appended to the notes the LLM agent reads |

## Contract

- Action and `joint_pos` state are the same packed vector: per arm
  `[j0..j5, gripper]`, joints in radians, gripper normalized (`0` closed,
  `1` open). Left arm first when both are selected.
- `reset()` connects on first use, remembers the pose the arm was found in,
  asks the operator to stand clear, ramps to `home_joints` over `ramp_secs`,
  then waits for the scene to be positioned.
- `step()` clamps to the declared bounds, sends `set_joint_positions` and
  `set_catch_pos` per arm, sleeps to `control_hz`, and reads back.
- `observe_parked()` ramps to rest before grading. `close()` ramps to rest
  and switches every arm to the SDK's protective mode.
- The framework's default guardrails (bounds clamp + delta limit derived from
  `joint_max_step` / `gripper_max_step`) apply to every policy action.

## What to confirm on the rig

The SDK's URDF declares placeholder joint limits (±10 rad), so the defaults
here are wide and rely on the arm's own hard stops. Before scored runs:

1. Positive direction of each joint (the SDK documents the right-hand rule
   about each axis; the LLM notes describe j0 as base yaw, j1/j2/j3 shoulder,
   elbow, wrist pitch about y, j4 wrist yaw, j5 tool roll).
2. The gripper's native readings when fully open and fully closed.
3. That `set_joint_positions` (SDK status 5) is honored on your firmware; the
   `--wiggle` check exercises exactly that path.
4. A `home_joints` pose that is safe to ramp to from a folded arm.
