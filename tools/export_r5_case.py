"""Export the verified 2026-09-19 R5 episode as a static, inspectable case.

Usage: python tools/export_r5_case.py /path/to/codex_cube_20260919_run3
Requires Pillow and numpy. Images retain the original RGB pixels in lossless
WebP. Only allowlisted episode fields are published; local machine paths stay
in the original logs. Editorial captions live separately in annotations.json.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil

import numpy as np
from PIL import Image


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def export(source, output):
    annotations = read(output / 'annotations.json')
    log_path = source / 'eval/r5-codex-cube-to-plate_e9b649de.json'
    log, result = read(log_path), read(source / 'result.json')
    assert log['samples'][0]['termination_reasons'] == ['done']
    assert result['physical_success'] and result['executed_env_steps'] == 1553
    (output / 'images').mkdir(exist_ok=True)
    (output / 'thumbs').mkdir(exist_ok=True)
    provenance = {'original_eval_sha256': digest(log_path), 'images': {}}

    def photo(input_path, target):
        im = Image.open(input_path).convert('RGB')
        im.save(output / target, lossless=True, method=6)
        assert np.array_equal(np.asarray(im), np.asarray(Image.open(output / target)))
        provenance['images'][target] = {'original_name': input_path.name,
                                       'original_sha256': digest(input_path),
                                       'published_sha256': digest(output / target)}
        return im

    observations = []
    raw = [read(p) for p in sorted((source / 'mailbox/obs').glob('*.json'))]
    assert len(raw) == 41 and [o['seq'] for o in raw] == list(range(1, 42))
    replies = [read(p) for p in sorted((source / 'mailbox/reply').glob('*.json'))]
    assert all(r['ok'] for r in replies)
    for o, caption in zip(raw, annotations['observations'], strict=True):
        seq = o['seq']
        assert caption['seq'] == seq
        reply, = [r for r in replies if r['obs_seq'] == seq]
        cmd = read(source / f'mailbox/cmd/{reply["cmd"]:06d}.json')
        stage, = [s for s in annotations['stages'] if s['first'] <= seq <= s['last']]
        images = {}
        for camera in ('fixed', 'wrist'):
            path = f'images/{seq:02d}-{camera}.webp'
            im = photo(source / f'mailbox/obs/{seq:06d}_{camera}.png', path)
            images[camera] = path
            if camera == 'fixed':
                im.resize((160, 120), Image.Resampling.LANCZOS).save(
                    output / f'thumbs/{seq:02d}.webp', quality=78, method=6)
        entry = {**caption, 'stage': stage['id'], 'env_step': o['env_step'],
                 'elapsed_s': round(o['time'] - raw[0]['time'], 3),
                 'captured_at': datetime.fromtimestamp(o['time'], timezone.utc).isoformat(),
                 'state': o['state'], 'last_commanded': o['commanded'],
                 'kinematics': o['kinematics'], 'approvals': o['approvals'],
                 'images': images, 'thumbnail': f'thumbs/{seq:02d}.webp',
                 'next_command': {**cmd, 'id': reply['cmd']}, 'reply': reply,
                 'next_observation': seq + 1 if seq < 41 else None,
                 'replay_s': o['env_step'] / 20}
        depth_path = source / ('depth_observe.npy' if seq == 6 else f'depth_{seq:06d}.npy')
        if depth_path.exists():
            depth = np.load(depth_path, allow_pickle=False)
            valid = np.isfinite(depth) & (depth > 0)
            # Fixed blue -> green -> orange scale, 5 cm near to 35 cm far.
            t = np.clip((np.nan_to_num(depth, nan=.05) - .05) / .30, 0, 1)
            colors = np.array([[42, 84, 192], [66, 179, 142], [239, 171, 77]])
            rgb = np.stack([np.interp(t, [0, .5, 1], colors[:, c]) for c in range(3)], -1)
            rgb[~valid] = 20
            depth_image = f'images/{seq:02d}-depth.webp'
            Image.fromarray(rgb.astype('uint8')).save(output / depth_image, lossless=True)
            entry['depth'] = {'image': depth_image, 'min_m': .05, 'max_m': .35,
                              'valid_fraction': round(float(valid.mean()), 4),
                              'source_sha256': digest(depth_path),
                              'note': '姿态保持期间额外采集；不是每次策略观测都包含深度。'}
        observations.append(entry)
    photo(source / 'final_fixed.png', 'images/final.webp')
    for camera in ('fixed', 'wrist'):
        shutil.copy2(source / f'video/cube-to-blue-plate-e0_{camera}.mp4', output / f'{camera}.mp4')
    data = {'schema_version': 1, 'id': 'r5-cube-20260919',
            'task': '把木块放进蓝色盘子', 'date': '2026-09-19',
            'stages': annotations['stages'], 'observations': observations,
            'summary': {'physical_success': True, 'grasp_attempts': 1,
                        'observation_count': 41, 'camera_image_count': 82,
                        'depth_checks': sum('depth' in o for o in observations),
                        'move_commands': 36, 'hold_commands': 4, 'stop_commands': 1,
                        'executed_steps': 1553, 'framework_total_steps': 1554,
                        'wall_duration_s': log['stats']['duration_s'],
                        'replay_fps': 20, 'replay_duration_s': 77.75,
                        'declared_control_hz': 20, 'rejected_commands': 0,
                        'guardrail_interventions': 0, 'startup_checks_ended_before_motion': 2},
            # eval records the calling workspace's Git HEAD. The editable
            # framework checkout was separately verified at this commit.
            'provenance': {'eval_worktree_git_commit': log['eval']['git_commit'],
                           'framework_git_commit': 'bb6495fe24432485246a108be0858d2f46e3475e',
                           'framework_version': log['eval']['inspect_robots_version'],
                           'decision_maker': '当前 Codex 会话',
                           'registered_policy': 'claude',
                           'policy_transport': '本地文件信箱',
                           'judgement': 'Codex 检查双路图像，确认放置和撤离后的实际结果',
                           'scorer': 'episode_length',
                           'eval_status': log['status'],
                           'original_eval_sha256': digest(log_path),
                           'runtime_source_sha256': result['runner_sha256']}}
    (output / 'data.json').write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    (output / 'provenance.json').write_text(json.dumps(provenance, indent=2) + '\n')
    print(f'Exported {len(observations)} observations, 82 lossless camera images, '
          f'{data["summary"]["depth_checks"]} depth checks, and 2 videos to {output}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_dir', type=Path)
    args = parser.parse_args()
    export(args.run_dir, Path(__file__).resolve().parents[1] / 'cases/r5-cube')
