"""Run-006 step-446 saved-input review. No station connections or tool dispatch.

Default: local FK replay and request fixtures. --infer: send two saved-input
requests to Astra; save returned proposals without executing any tool.
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path

import astra_run as run
import httpx
import numpy as np
from offline_prompt_decision_audit import restore_blobs

ARCHIVE = run.HERE / 'astra-yam3-run-006'
OUT = run.HERE / 'offline-review/run-006-prompt-revision'


def decisions():
    return [json.loads(line) for line in (ARCHIVE / 'station-events.jsonl').read_text().splitlines()
            if json.loads(line)['kind'] == 'decision_observation']


def fk_review():
    # Same model assembler as get_yam_robot; importing does not construct a robot.
    import mujoco
    from i2rt.robots.utils import ArmType, GripperType, combine_arm_and_gripper_xml
    path = Path(combine_arm_and_gripper_xml(ArmType.YAM_PRO, GripperType.LINEAR_4310))
    model = mujoco.MjModel.from_xml_path(str(path))
    assembled_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    path.unlink()
    data = mujoco.MjData(model)
    site = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, 'grasp_site')
    arm_ids = [mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, f'joint{i+1}') for i in range(6)]
    def pose(q):
        for j, v in zip(arm_ids, q[:6]):
            data.qpos[model.jnt_qposadr[j]] = v
        for name in ('joint7', 'joint8'):
            j = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name)
            lo, hi = model.jnt_range[j]
            data.qpos[model.jnt_qposadr[j]] = lo + q[6] * (hi-lo)
        mujoco.mj_forward(model, data)
        return data.site_xpos[site].copy(), data.site_xmat[site].reshape(3, 3).copy()
    history = []
    for event in decisions():
        p, _ = pose(event['measured_joints'])
        history.append(dict(step=event['step'], grasp_site_base_m=p.tolist()))
    start = np.asarray(decisions()[-1]['measured_joints'])
    p0, r0 = pose(start)
    candidates = []
    deltas = [(f'J{j}{sign:+d}', {j: sign*.12}) for j in range(6) for sign in (-1, 1)]
    deltas += [('J1+0.12,J2+0.12', {1:.12, 2:.12}),
               ('J1-0.12,J2-0.12', {1:-.12, 2:-.12}),
               ('J1+0.12,J2+0.06', {1:.12, 2:.06})]
    for name, changes in deltas:
        dq = np.zeros(7)
        for j, delta in changes.items(): dq[j] = delta
        trajectory = []
        # Kinematic interpolation only; no physics stepping/contact scene.
        for alpha in np.linspace(0, 1, 41):
            q = start + alpha*dq
            p, r = pose(q)
            trajectory.append(p)
        angle = np.arccos(np.clip((np.trace(r @ r0.T)-1)/2, -1, 1))
        endpoint = start+dq
        in_limits = all(model.jnt_range[j, 0] <= endpoint[i] <= model.jnt_range[j, 1]
                        for i, j in enumerate(arm_ids))
        candidates.append(dict(name=name, delta_rad=dq.tolist(),
            delta_base_mm=((p-p0)*1000).tolist(), rotation_deg=float(np.degrees(angle)),
            within_nominal_model_limits=bool(in_limits),
            path_base_m=np.asarray(trajectory).tolist()))
    return dict(runtime='MuJoCo '+mujoco.__version__+'; model-only mj_forward',
        model='YAM_PRO + LINEAR_4310; existing station model assembly',
        assembled_model_sha256=assembled_hash, frame='model robot base; NOT camera or table frame',
        camera_to_base='UNKNOWN; no measured transform used',
        limitations='Nominal grasp_site, not measured fingertips. No table/block/other-arm '
                    'scene, camera registration, collision validation or physical height guarantee. '
                    '41-sample kinematic replay validates predicted model motion only.',
        robot_dispatches=0, history=history, candidates=candidates)


def fixtures(fk):
    wire = next((ARCHIVE / 'wire').rglob('calls.jsonl'))
    calls = [json.loads(line) for line in wire.read_text().splitlines()]
    assert len(run.PROMPT.read_text().strip()) <= 8192
    body = copy.deepcopy(calls[-1]['request'])
    old = (ARCHIVE / 'prompt.txt').read_text().strip()
    assert body['input'][0]['content'].count(old) == 1
    body['input'][0]['content'] = body['input'][0]['content'].replace(old, run.PROMPT.read_text().strip())
    # Everything after the system prompt, including image horizon/history,
    # remains identical to the archived final request. No give_up response fed back.
    assert body['input'][1:] == calls[-1]['request']['input'][1:]
    with_fk = copy.deepcopy(body)
    compact = [{k:v for k,v in c.items() if k not in ('path_base_m', 'delta_rad')}
               for c in fk['candidates']]
    with_fk['input'].append({'role':'user', 'content':[{'type':'input_text', 'text':
        'Offline reviewer evidence (model-only, not a live observation): '+
        json.dumps({k:fk[k] for k in ('model','frame','camera_to_base','limitations')})+
        '\nCandidate comparisons at measured step-446 pose: '+json.dumps(compact)+
        '\nUse this only to distinguish reach/rotation hypotheses. The camera-to-base '
        'and table-to-base transforms remain unknown. No candidate is certified clear. '
        'Choose the next decision from the saved views under the revised prompt.'}]})
    return {'step446_prompt_only': body, 'step446_with_offline_fk': with_fk}, wire.parent.parent/'blobs'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--infer', action='store_true')
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    fk = fk_review()
    (OUT/'fk-review.json').write_text(json.dumps(fk, indent=2)+'\n')
    requests, blobs = fixtures(fk)
    (OUT/'requests.json').write_text(json.dumps(requests, indent=2)+'\n')
    manifest = dict(mode='saved-input; dispatch disabled', robot_connections=0, robot_dispatches=0,
        step=446, prompt_sha256=hashlib.sha256(run.PROMPT.read_bytes()).hexdigest(),
        source_wire_sha256=hashlib.sha256(next((ARCHIVE/'wire').rglob('calls.jsonl')).read_bytes()).hexdigest(),
        current_camera_hashes=decisions()[-1]['cameras'], results={})
    if args.infer:
        for name, body in requests.items():
            print('Saved-input inference: '+name, flush=True)
            try:
                with httpx.Client(transport=run.GatewayTransport(), timeout=120.) as client:
                    response = client.post(run.URL, headers={
                        'Authorization':'Bearer '+os.environ['RHOS_API_KEY']},
                        json=restore_blobs(body, blobs))
                response.raise_for_status()
                payload = response.json()
                (OUT/(name+'.response.json')).write_text(json.dumps(payload, indent=2)+'\n')
                calls = [x for x in payload.get('output',[]) if x.get('type')=='function_call']
                manifest['results'][name] = [dict(tool=c['name'], arguments=json.loads(c['arguments'])) for c in calls]
                print(json.dumps(manifest['results'][name]), flush=True)
            except (httpx.HTTPError, KeyError) as exc:
                # Never print request/headers/credential material on failure.
                manifest['results'][name] = dict(status='inference_unavailable', error_type=type(exc).__name__)
                print(name+': '+type(exc).__name__, flush=True)
            (OUT/'saved-input-result.json').write_text(json.dumps(manifest, indent=2)+'\n')
    else:
        (OUT/'fixture-manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
        print('Built step-446 fixtures and local FK replay; no network or station access.')


if __name__ == '__main__':
    main()
