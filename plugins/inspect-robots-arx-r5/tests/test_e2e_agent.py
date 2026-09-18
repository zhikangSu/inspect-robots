"""End to end through the real eval() with the LLM agent policy on a fake R5.

A scripted fake LLM (httpx.MockTransport) issues one move_joints and then
done; the whole framework path runs: bind, compatibility, guardrails,
speed-limited chunk synthesis, rollout, observe_parked, log persistence.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from _fakes import FakeArm

from inspect_robots import eval as ir_eval
from inspect_robots.approver import ChainApprover, ClampApprover, DeltaLimitApprover
from inspect_robots.scene import Scene
from inspect_robots.scorer import episode_length
from inspect_robots.task import Task

pytest.importorskip("inspect_robots_agent")
from inspect_robots_agent import LLMAgentPolicy


def _tool_response(name: str, arguments: dict[str, object]) -> dict[str, object]:
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": f"call_{name}",
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(arguments)},
                        }
                    ],
                }
            }
        ]
    }


def test_agent_drives_fake_r5_through_eval(  # type: ignore[no-untyped-def]
    make_embodiment, arms: dict[str, FakeArm], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_KEY", "x")
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            payload = _tool_response(
                "move_joints",
                {
                    "targets": {"left_j0": 0.3, "right_gripper": 0.0},
                    "note": "Cube is to the left: turn the left base, close the right gripper.",
                },
            )
        else:
            payload = _tool_response(
                "done", {"summary": "moved", "hindsight": "left_j0 positive turns left."}
            )
        return httpx.Response(200, json=payload)

    policy = LLMAgentPolicy(
        model="openai/fake",
        base_url="https://fake.local/v1",
        api_key_env="FAKE_KEY",
        transport=httpx.MockTransport(handler),
        wire_capture=False,
    )
    emb = make_embodiment(unattended=True, joint_max_step=0.05)
    space = emb.info.action_space
    task = Task(
        name="r5-smoke",
        scenes=[Scene(id="s0", instruction="pick up the cube")],
        scorer=episode_length(),
        max_steps=200,
    )
    (log,) = ir_eval(
        task,
        policy,
        emb,
        log_dir=str(tmp_path),
        approver=ChainApprover(ClampApprover(space), DeltaLimitApprover(space)),
    )
    assert log.status == "success", log.error
    # The tool schema the model saw names the R5 dimensions and carries the rig notes.
    tools = requests[0]["tools"]
    assert isinstance(tools, list)
    move = next(t for t in tools if t["function"]["name"] == "move_joints")
    assert "left_j0" in move["function"]["parameters"]["properties"]["targets"]["description"]
    system = requests[0]["messages"][0]["content"]  # type: ignore[index]
    assert "ARX R5" in system
    # The arm ended where the model asked, via a speed-limited multi-step chunk.
    assert arms["left"].joints[0] == pytest.approx(0.3)
    assert len(arms["left"].joint_commands) > 3
    assert arms["right"].catch_commands[-1] == pytest.approx(0.0)
    # observe_parked ran before grading (arms parked back at their initial pose)...
    # ...then close() is the caller's job for a caller-constructed embodiment.
    assert log.samples[0].termination_reasons[0] == "done"
    emb.close()
    assert arms["left"].protected
