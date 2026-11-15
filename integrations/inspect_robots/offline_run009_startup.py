"""Replay saved Run 009 startup through the real policy using a mock model.

No inference, station client, dispatch, latch or trial launch. Socket access is
blocked. Artifacts show exactly the revised request, tools and instructions.
"""
import copy
import io
import json
from pathlib import Path
import re
from unittest.mock import patch

import astra_run as run
import httpx
import numpy as np
from PIL import Image
from demo import offline_only
from planned_motion import trajectory
from test_astra_run import make_arm

ARCHIVE = run.HERE / 'astra-yam3-run-009'
OUT = run.HERE / 'offline-review/run-009-startup'


def replay(output=OUT):
    events = [json.loads(line) for line in (ARCHIVE / 'station-events.jsonl').read_text().splitlines()]
    decision = next(e for e in events if e['kind'] == 'decision_observation')
    old_request = json.loads(next((ARCHIVE / 'wire').rglob('calls.jsonl')).read_text().splitlines()[0])['request']
    bounds = re.findall(r'(right_\w+): \[([^,]+), ([^\]]+)\]', old_request['input'][0]['content'])
    assert [b[0] for b in bounds] == list(run.LABELS)
    status = {'joint_limits': [[-3., 3.]] * 7 + [[float(lo), float(hi)] for _, lo, hi in bounds]}
    arm, robot = make_arm()
    arm = run.StationArm(robot, arm.preview, status)
    scene = run.Scene(id='offline-run009-startup', instruction='Use the right arm to place the pink block fully inside the white paper marked X.')
    arm.reset(scene)
    arm.last = np.array(decision['target'])
    robot.joints[7:] = decision['measured_joints']
    images = {}
    for view in ('main', 'left', 'right'):
        digest = decision['cameras'][view]['sha256']
        raw = (ARCHIVE / 'replay-frames' / (digest + '.jpg')).read_bytes()
        assert run.hashlib.sha256(raw).hexdigest() == digest
        images[view] = np.array(Image.open(io.BytesIO(raw)).convert('RGB'))
    crop = np.array(Image.fromarray(images['main']).crop((96, 192, 544, 448)).resize((640, 366), Image.Resampling.LANCZOS))
    images['main_comparison'] = np.concatenate((crop, crop), axis=1)
    extra = copy.deepcopy(decision)
    extra['motion_report']['lines'][:2] = [run.OBSERVATION_GUIDANCE, arm.motion_allowance()]
    extra.update(env_step=0, guard_reference=arm.last.tolist())
    observation = run.Observation(images=images, state={'joint_pos': robot.joints[7:].copy()}, extra=extra)
    requests = []
    target = arm.last.copy()
    target[0] += .02

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={'status': 'completed', 'output': [{
            'type': 'function_call', 'call_id': 'offline-fixture', 'name': 'move_joints',
            'arguments': json.dumps({'targets': {run.LABELS[0]: float(target[0])},
                                    'note': 'Offline numeric probe fixture; physical clearance is not certified.'})}]})

    with offline_only():
        policy = run.make_policy(run.GatewayTransport(httpx.MockTransport(respond)), arm.pre_check,
                                 station=arm, env={'RHOS_API_KEY': 'offline-placeholder'})
        policy.bind(arm.info)
        policy._max_steps = run.MAX_STEPS
        policy.reset(scene)
        # Saved observations replace live refresh; they are not fresh exposures.
        with patch.object(arm, 'decision_observation', return_value=observation):
            chunk = policy.act(observation)
        assert len(requests) == 1 and chunk.actions and not chunk.terminal
        assert np.isclose(chunk.actions[-1].data[0] - arm.last[0], .02)
        names = [t['name'] for t in requests[0]['tools']]
        assert 'move_joints' in names
        assert 'move_gripper_to_pose' not in names and 'set_gripper' not in names
        # Use the normalized open endpoint for a full-range gripper boundary test.
        arm.last[6] = robot.joints[13] = 1.0
        cap_checks = []
        for phase in run.MOTION_PHASES:
            arm.motion_phase = phase
            for dimension, limit in [(0, run.MAX_MOVE_RAD), (6, run.MAX_GRIPPER_DELTA)]:
                for over in (False, True):
                    goal = arm.last.copy()
                    # Gripper starts open: test closing direction.
                    goal[dimension] += (-1 if dimension == 6 else 1) * (limit + (1e-5 if over else 0))
                    error = arm.pre_check(trajectory(arm.last, goal))
                    assert bool(error) == over, (phase, dimension, over, error)
                    cap_checks.append(dict(phase=phase, dimension=run.LABELS[dimension],
                                           displacement=limit + (1e-5 if over else 0), accepted=error is None))
        assert not robot.commands
    output.mkdir(parents=True, exist_ok=True)
    (output / 'request.json').write_text(json.dumps(requests[0], indent=2) + '\n')
    (output / 'tools.json').write_text(json.dumps(requests[0]['tools'], indent=2) + '\n')
    (output / 'instructions.txt').write_text(requests[0]['input'][0]['content'] + '\n')
    (output / 'observation-report.txt').write_text('\n'.join(extra['motion_report']['lines']) + '\n')
    result = dict(mode='saved Run 009 startup; mock model response, no fresh Astra inference',
                  tools=names, motion_allowance=arm.motion_allowance(), cap_checks=cap_checks,
                  mock_next_action='move_joints', robot_dispatches=len(robot.commands),
                  network_requests=0, trial_launched=False,
                  limitation='Numeric feasibility only; saved views do not certify physical clearance.')
    (output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


if __name__ == '__main__':
    print(json.dumps(replay(), indent=2))
