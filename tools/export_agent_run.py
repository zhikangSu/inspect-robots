"""Export one LLM-agent run into the static case the page's right-hand panels read.

Usage:
    python3 tools/export_agent_run.py LOG_DIR cases/NAME [--log FILE] [--video DIR]

LOG_DIR is the --log-dir of an ``inspect-robots ... --policy agent`` run. The
tool reads the EvalLog JSON, the wire capture (every request and response
exactly as it was sent), the executed-action sidecar, and optional MP4s made
by ``inspect-robots video LOG --out DIR``. It writes into cases/NAME:

    run.json       the per-step, per-turn digest the page renders
    wire.json      every captured request and response; images point at img/
    eval-log.json  the EvalLog itself, with local absolute paths removed
    actions.jsonl  every executed step, copied from the action sidecar
    img/           the camera frames the model received (JPEG copies via sips)
    video/         optional replay videos, one per camera

The capture holds request bodies only, so no API key is ever copied.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

BLOB_RE = re.compile(r"\$blob:([0-9a-f]{64})")
CAMERA_RE = re.compile(r"^camera '([^']+)' \(step (\d+)\):$")
ELIDED_RE = re.compile(r"^\[(\d+) camera frame\(s\) elided\]$")
EXECUTING_RE = re.compile(r"^executing (\S+) over (\d+) steps? \(([\d.]+)s\)")
APPROVER_RE = re.compile(r"^approver: (\d+) step\(s\) modified")
STOP_TOOLS = ("done", "give_up")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def find_log(log_dir: Path, explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit
    candidates = []
    for path in log_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        samples = data.get("samples") if isinstance(data, dict) else None
        if samples and any("wire_capture" in m for m in samples[0].get("trial_metadata", [])):
            candidates.append((data.get("eval", {}).get("created", ""), path))
    if not candidates:
        raise SystemExit(f"no EvalLog with a wire capture found in {log_dir}")
    return sorted(candidates)[-1][1]


def parse_observation(blocks: list[dict]) -> dict:
    """Split one 'Current observation.' message into lines, state, and camera frames."""
    text = blocks[0]["text"]
    obs: dict = {"text": text, "lines": text.split("\n"), "state": {}, "images": [],
                 "elided": 0, "approver": None, "operator": [], "extra": []}
    for line in obs["lines"][1:]:
        if line.startswith("state[") and "]: " in line:
            key, _, values = line.partition("]: ")
            obs["state_key"] = key[len("state["):]
            for pair in values.split():
                name, _, value = pair.partition("=")
                try:
                    obs["state"][name] = float(value)
                except ValueError:
                    pass
        elif line.startswith("Instruction: "):
            obs["instruction"] = line[len("Instruction: "):]
        elif APPROVER_RE.match(line):
            obs["approver"] = line
        elif line.startswith("operator feedback"):
            obs["operator"].append(line)
        else:
            obs["extra"].append(line)
    camera = step = None
    for block in blocks[1:]:
        if block.get("type") == "text":
            label = CAMERA_RE.match(block["text"])
            elided = ELIDED_RE.match(block["text"])
            if label:
                camera, step = label.group(1), int(label.group(2))
            elif elided:
                obs["elided"] += int(elided.group(1))
            else:
                obs["extra"].append(block["text"])
        elif block.get("type") == "image":
            data = json.dumps(block)
            match = BLOB_RE.search(data)
            obs["images"].append({"camera": camera, "step": step,
                                  "blob": match.group(1) if match else None})
    steps = [image["step"] for image in obs["images"] if image["step"] is not None]
    obs["step"] = steps[0] if steps else None
    return obs


def classify(message: dict) -> tuple[str, object]:
    content = message.get("content")
    if message.get("role") != "user":
        return "other", message
    if isinstance(content, str):
        return ("goal", content) if content.startswith("Goal:") else ("nudge", content)
    if isinstance(content, list) and content:
        first = content[0]
        if first.get("type") == "text" and first.get("text", "").startswith("Current observation."):
            return "observation", parse_observation(content)
        if all(block.get("type") == "tool_result" for block in content):
            results = {}
            for block in content:
                value = block.get("content")
                if isinstance(value, list):
                    value = "".join(part.get("text", "") for part in value if isinstance(part, dict))
                results[block["tool_use_id"]] = {"content": value, "is_error": bool(block.get("is_error"))}
            return "results", results
        if all(block.get("type") == "text" for block in content):
            return "nudge", "".join(block["text"] for block in content)
    return "other", message


def parse_response(response: object) -> dict:
    if not isinstance(response, dict):
        return {"raw": response}
    out: dict = {"thinking": [], "text": [], "tool_uses": [], "stop_reason": response.get("stop_reason"),
                 "usage": response.get("usage") or {}, "model": response.get("model")}
    for block in response.get("content") or []:
        kind = block.get("type")
        if kind == "thinking":
            if block.get("thinking"):
                out["thinking"].append(block["thinking"])
        elif kind == "redacted_thinking":
            out["thinking"].append("[redacted]")
        elif kind == "text":
            out["text"].append(block.get("text", ""))
        elif kind == "tool_use":
            out["tool_uses"].append({"id": block.get("id"), "name": block.get("name"),
                                     "input": block.get("input")})
    return out


def to_jpeg(src: Path, dest: Path) -> str:
    """Write a web copy of one captured frame; fall back to the PNG when sips is missing."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if shutil.which("sips"):
        jpg = dest.with_suffix(".jpg")
        done = subprocess.run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", "85",
                               str(src), "--out", str(jpg)], capture_output=True)
        if done.returncode == 0 and jpg.exists():
            return jpg.name
    png = dest.with_suffix(".png")
    shutil.copyfile(src, png)
    return png.name


def list_files(log_dir: Path) -> list[dict]:
    """Relative paths and sizes in the log directory; image blobs and frames are summarised."""
    groups: dict[str, dict] = {}
    for path in sorted(p for p in log_dir.rglob("*") if p.is_file()):
        rel = path.relative_to(log_dir).as_posix()
        if path.suffix in (".png", ".npy") and path.parent != log_dir:
            key = path.parent.relative_to(log_dir).as_posix() + "/"
            group = groups.setdefault(key, {"path": key, "count": 0, "size": 0})
            group["count"] += 1
            group["size"] += path.stat().st_size
        else:
            groups[rel] = {"path": rel, "size": path.stat().st_size}
    return list(groups.values())


def export(log_dir: Path, out: Path, log_path: Path, video_dir: Path | None, rig: dict | None = None) -> None:
    log = json.loads(log_path.read_text())
    sample = log["samples"][0]
    trial = sample["trial_metadata"][0]
    calls_path = log_dir / trial["wire_capture"]
    blob_dir = calls_path.parent.parent / "blobs"
    rows = [json.loads(line) for line in calls_path.read_text().splitlines() if line.strip()]
    if out.exists():
        for sub in ("img", "video"):
            shutil.rmtree(out / sub, ignore_errors=True)
    out.mkdir(parents=True, exist_ok=True)

    # One logical call can hold several attempts (HTTP retries); the last one is what the
    # policy used, earlier ones are kept as failed attempts.
    by_call: dict[int, list[dict]] = {}
    for row in rows:
        by_call.setdefault(row["call"], []).append(row)

    turns: list[dict] = []
    goal = None
    prev_messages = 0
    call_records: list[dict] = []
    for index in sorted(by_call):
        attempts = by_call[index]
        final = attempts[-1]
        messages = final["request"]["messages"]
        fresh = messages if not call_records else messages[prev_messages + 1:]
        prev_messages = len(messages)
        images = elided = 0
        for message in messages:
            for block in message.get("content") if isinstance(message.get("content"), list) else []:
                if block.get("type") == "image":
                    images += 1
                elif block.get("type") == "text" and ELIDED_RE.match(block.get("text", "")):
                    elided += int(ELIDED_RE.match(block["text"]).group(1))
        record = {"call": index, "duration_s": round(final.get("duration_s") or 0, 3),
                  "status": final.get("status"), "t": final.get("t"),
                  "attempts": [{"status": a.get("status"), "error": a.get("error")} for a in attempts[:-1]],
                  "n_messages": len(messages), "n_images": images, "n_elided": elided,
                  "response": parse_response(final.get("response")),
                  "input": []}
        observation = None
        for message in fresh:
            kind, value = classify(message)
            if kind == "goal":
                goal = value
            elif kind == "observation":
                observation = value
            elif kind == "results" and call_records:
                call_records[-1]["results"] = value
            elif kind == "nudge" and call_records:
                call_records[-1]["followup"] = value
            record["input"].append(kind)
        if observation is not None or not turns:
            turns.append({"obs": observation, "calls": []})
        turns[-1]["calls"].append(record)
        call_records.append(record)

    # The final call's tool results never reach another request; take them from the transcript.
    transcript = (sample.get("policy_transcripts") or [[]])[0]
    tool_messages = {m.get("tool_call_id"): m.get("content") for m in transcript if m.get("role") == "tool"}
    for record in call_records:
        if "results" not in record and record["response"].get("tool_uses"):
            record["results"] = {use["id"]: {"content": tool_messages.get(use["id"]), "is_error": False}
                                 for use in record["response"]["tool_uses"]}

    actions_path = log_dir / trial["actions"]
    labels: list[str] = []
    executed: dict[int, list[float]] = {}
    for line in actions_path.read_text().splitlines():
        entry = json.loads(line)
        if entry.get("kind") == "header":
            labels = entry["labels"]
        elif "t" in entry:
            executed[entry["t"]] = [round(float(v), 4) for v in entry["action"]]
    total_steps = log["stats"]["total_steps"]

    blob_names: dict[str, str] = {}
    for number, turn in enumerate(turns, start=1):
        turn["n"] = number
        for image in (turn["obs"] or {}).get("images", []):
            sha = image.pop("blob")
            if sha and sha not in blob_names:
                blob_names[sha] = to_jpeg(blob_dir / f"{sha}.png", out / "img" / f"t{number:02d}-{image['camera']}")
            image["src"] = f"img/{blob_names[sha]}" if sha else None

    for number, turn in enumerate(turns):
        nxt = turns[number + 1] if number + 1 < len(turns) else None
        last = turn["calls"][-1]
        accepted = None
        for use in last["response"].get("tool_uses", []):
            result = (last.get("results") or {}).get(use["id"]) or {}
            content = result.get("content") or ""
            played = EXECUTING_RE.match(content)
            if played or use["name"] in STOP_TOOLS:
                accepted = {"name": use["name"], "input": use["input"], "result": content,
                            "steps": int(played.group(2)) if played else None,
                            "seconds": float(played.group(3)) if played else None}
                break
        turn["accepted"] = accepted
        start = (turn["obs"] or {}).get("step") or 0
        end = (nxt["obs"] or {}).get("step") if nxt else total_steps
        turn["steps"] = [start, end]
        turn["executed"] = [executed[t] for t in range(start, end) if t in executed]
        turn["after_approver"] = (nxt["obs"] or {}).get("approver") if nxt else None
        if accepted and isinstance(accepted["input"], dict) and nxt and nxt["obs"]:
            targets = accepted["input"].get("targets") or {}
            after = nxt["obs"]["state"]
            turn["residual"] = {k: round(after[k] - float(v), 4) for k, v in targets.items() if k in after}

    def swap_blob(match: re.Match) -> str:
        name = blob_names.get(match.group(1))
        return f"img/{name}" if name else "(image not exported)"

    wire = json.loads(BLOB_RE.sub(swap_blob, json.dumps(rows)))
    first_request = rows[0]["request"]
    system = first_request.get("system")
    if isinstance(system, list):
        system = "".join(part.get("text", "") for part in system)

    ev = log["eval"]
    videos = {}
    if video_dir is not None:
        (out / "video").mkdir(exist_ok=True)
        for mp4 in sorted(video_dir.glob("*.mp4")):
            camera = mp4.stem.rsplit("_", 1)[-1]
            shutil.copyfile(mp4, out / "video" / f"{camera}.mp4")
            videos[camera] = f"video/{camera}.mp4"

    usage = trial.get("llm_usage") or {}
    run = {
        "schema": 1,
        "meta": {
            "created": ev.get("created"), "version": ev.get("inspect_robots_version"),
            "embodiment": ev.get("embodiment"), "control_hz": (ev.get("embodiment_info") or {}).get("control_hz"),
            "is_simulated": (ev.get("embodiment_info") or {}).get("is_simulated"),
            "policy": ev.get("policy"), "policy_config": ev.get("policy_config"),
            "task": ev.get("task"), "max_steps": ev.get("max_steps"), "instruction": sample.get("instruction"),
            "status": log.get("status"), "termination": (sample.get("termination_reasons") or [None])[0],
            "judgement": (sample.get("operator_judgements") or [None])[0],
            "judgement_note": (sample.get("operator_notes") or [None])[0],
            "judgement_source": (sample.get("judgement_sources") or [None])[0],
            "metrics": (log.get("results") or {}).get("metrics"),
            "duration_s": log["stats"].get("duration_s"), "total_steps": total_steps,
            "hindsight": trial.get("hindsight"), "llm_usage": usage,
            "endpoint": rows[0].get("endpoint"), "base_url": (ev.get("policy_config") or {}).get("base_url"),
            "source_sha256": {"eval_log": sha256(log_path), "wire_capture": sha256(calls_path)},
            "frames_dir": Path(log["stats"]["frames_dir"]).name if log["stats"].get("frames_dir") else None,
            "rig": rig or {},
        },
        "files": list_files(log_dir),
        "system": system, "goal": goal, "labels": labels,
        "request_settings": {k: v for k, v in first_request.items() if k not in ("messages", "system", "tools")},
        "tools": first_request.get("tools", []),
        "turns": turns, "videos": videos,
    }
    (out / "run.json").write_text(json.dumps(run, ensure_ascii=False, indent=1) + "\n")
    (out / "wire.json").write_text(json.dumps(wire, ensure_ascii=False) + "\n")
    clean = json.loads(json.dumps(log))
    frames_dir = clean.get("stats", {}).get("frames_dir")
    if frames_dir:
        clean["stats"]["frames_dir"] = Path(frames_dir).name
    (out / "eval-log.json").write_text(json.dumps(clean, ensure_ascii=False, indent=1) + "\n")
    shutil.copyfile(actions_path, out / "actions.jsonl")
    print(f"{len(turns)} turns, {len(call_records)} calls, {len(blob_names)} images, "
          f"{total_steps} steps -> {out}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("log_dir", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--log", type=Path, help="EvalLog JSON to use when LOG_DIR holds several")
    parser.add_argument("--video", type=Path, help="directory with MP4s from `inspect-robots video --out`")
    parser.add_argument("--rig", type=Path, help="JSON of rig settings the log does not record "
                        "(e.g. the -E values: joint_max_step, gripper_max_step)")
    args = parser.parse_args()
    rig = json.loads(args.rig.read_text()) if args.rig else None
    export(args.log_dir, args.out, find_log(args.log_dir, args.log), args.video, rig)


if __name__ == "__main__":
    main()
