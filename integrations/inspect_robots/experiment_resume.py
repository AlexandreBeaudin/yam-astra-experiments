"""Durable model-boundary checkpoints; never replay a saved motion chunk."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import time

import numpy as np

from experiment_budgets import MAX_CALLS, MAX_STEPS


def atomic_json(path, payload):
    path = Path(path)
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as stream:
        json.dump(payload, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def restore_blobs(value, directory):
    if isinstance(value, dict):
        return {k: restore_blobs(v, directory) for k, v in value.items()}
    if isinstance(value, list):
        return [restore_blobs(v, directory) for v in value]
    if isinstance(value, str):
        def replace(match):
            digest = match.group(1)
            raw = (directory / f'{digest}.png').read_bytes()
            if hashlib.sha256(raw).hexdigest() != digest:
                raise ValueError('wire image checksum mismatch')
            return base64.b64encode(raw).decode()
        return re.sub(r'\$blob:([0-9a-f]{64})', replace, value)
    return value


def canonical_history(items):
    """Invert our Responses encoder, retaining opaque reasoning/output verbatim."""
    messages, raw, pending = [], {}, []
    for item in items:
        kind = item.get('type')
        if kind in ('reasoning', 'message', 'function_call'):
            pending.append(copy.deepcopy(item))
            if kind == 'function_call':
                call_id = item['call_id']
                raw[call_id] = pending
                pending = []
                messages.append(dict(role='assistant', content=None, tool_calls=[dict(
                    id=call_id, type='function', function=dict(
                        name=item['name'], arguments=item['arguments']))]))
            continue
        if pending:
            raise ValueError('unmatched Responses reasoning/output items')
        if kind == 'function_call_output':
            messages.append(dict(role='tool', tool_call_id=item['call_id'], content=item['output']))
            continue
        if item.get('role') not in ('system', 'developer', 'user', 'assistant'):
            raise ValueError('unsupported Responses input item')
        content = item['content']
        if isinstance(content, list):
            translated = []
            for part in content:
                if part['type'] == 'input_text':
                    translated.append(dict(type='text', text=part['text']))
                elif part['type'] == 'input_image':
                    translated.append(dict(type='image_url', image_url=dict(url=part['image_url'])))
                else:
                    raise ValueError('unsupported Responses content')
            content = translated
        messages.append(dict(role=item['role'], content=content))
    if pending or not messages or messages[-1]['role'] != 'user':
        raise ValueError('checkpoint must end at an unanswered observation')
    return messages, raw



def recover_motion_boundary(source, saved, *, recover_tracking_stop=False, recover_rejected_motion=False,
                            recover_separation_stop=False):
    """Reconcile an evidenced stop; never execute the failed trajectory.

    Requires a complete acceptance journal, successful final hold, the final
    model response, and nearby recorded feedback. Live identity/pose/hold checks
    remain mandatory in the runner. Original evidence is never rewritten.
    """
    result = json.loads((source / 'attempt-result.json').read_text())
    if result.get('evaluation_completed') or result.get('final_hold_confirmed') is not True:
        raise ValueError('motion recovery requires an interrupted run with confirmed final hold')
    framework = json.loads((source / 'framework-log.json').read_text())
    errors = [str(sample.get('error', '')) for sample in framework.get('samples', [])]
    tracking_stop = recover_tracking_stop and errors == ['PolicyError: arm tracking residual exceeds 0.08 rad at decision boundary']
    rejected_motion = recover_rejected_motion and errors == ['EmbodimentFault: waypoint exceeds declared per-step change']
    separation_stop = (recover_separation_stop and saved.get('control_mode') == 'active_perception'
                       and errors == ['SafetyAbort: inter-arm interpolated separation cannot be certified'])
    if recover_separation_stop and not separation_stop:
        raise ValueError('separation recovery requires the recorded observer interpolation-certificate stop')
    if not tracking_stop and not rejected_motion and not separation_stop and 'episode wall-clock budget exhausted' not in str(framework.get('error', '')):
        raise ValueError('motion recovery requires a supported timeout or explicitly reviewed tracking stop')
    events = [json.loads(line) for line in (source / 'station-events.jsonl').read_text().splitlines()]
    boundary = [e for e in events if e['kind'] == 'decision_observation'][-1]
    if boundary['step'] != saved['step']:
        raise ValueError('checkpoint does not match last decision boundary')
    dispatches = [e for e in events if e['kind'] == 'command_dispatch' and e['step'] > saved['step']]
    accepts = [e for e in events if e['kind'] == 'command_acceptance' and e['step'] > saved['step']]
    if rejected_motion and (dispatches or accepts):
        raise ValueError('rejected-motion recovery requires zero dispatched commands')
    if not rejected_motion and (not dispatches or len(dispatches) != len(accepts)):
        raise ValueError('uncertain command outcome after interruption')
    indices = np.r_[0:6, 7:14] if separation_stop else np.arange(7, 14)
    previous = np.asarray(saved['initial'], dtype=float).copy()
    previous[indices] = saved['target']
    if separation_stop and (not np.array_equal(boundary.get('target'), saved['target'])
                            or not np.array_equal(boundary.get('measured_joints'), saved['measured'])):
        raise ValueError('observer checkpoint does not match recorded decision pose')
    step_limits = np.array([.003]*12 + [.02]) if separation_stop else np.array([.003]*6 + [.02])
    for step, (dispatch, accepted) in enumerate(zip(dispatches, accepts), saved['step'] + 1):
        target = np.asarray(accepted['applied'], dtype=float)
        if (dispatch['command_id'] != accepted['command_id']
                or dispatch['step'] != step or accepted['step'] != step
                or accepted.get('accepted') is not True or accepted.get('limited') is not False
                or target.shape != (14,) or not np.all(np.isfinite(target))
                or not np.array_equal(target, dispatch['target'])
                or not np.array_equal(target, accepted['target'])
                or (abs(target[6] - saved['initial'][6]) > 1e-9 if separation_stop else
                    np.max(np.abs(target[:7] - saved['initial'][:7])) > .008)
                or np.any(np.abs(target[indices] - previous[indices]) > step_limits + 1e-8)):
            raise ValueError('inconsistent or modified command journal')
        previous = target
    last = accepts[-1] if accepts else dict(step=saved['step'])
    observations = [e for e in events if e['kind'] == 'observation' and e['step'] == last['step'] - (0 if tracking_stop or rejected_motion or separation_stop else 1)]
    if not observations:
        raise ValueError('missing feedback immediately before final accepted waypoint')
    measured = np.asarray(observations[-1]['measured_joints'], dtype=float)
    arm_slice = slice(0, 12) if separation_stop else slice(None)
    if (measured.shape != (len(indices),) or not np.all(np.isfinite(measured))
            or (not tracking_stop and np.max(np.abs(measured[arm_slice] - previous[indices][arm_slice])) > .08)):
        raise ValueError('last recorded feedback does not match accepted target')
    if separation_stop:
        # A held object prevents commanded closure; it is not arm tracking lag.
        if not 0 <= measured[-1] <= 1 or not 0 <= previous[-1] <= .90:
            raise ValueError('invalid recorded observer gripper state')
        rejection = [e for e in events if e['kind'] == 'inter_arm_rejection'][-1]
        if (rejection.get('reason') != 'inter-arm interpolated separation cannot be certified'
                or not boundary['recorded_time_ns'] < dispatches[0]['recorded_time_ns']
                or not last['recorded_time_ns'] <= observations[-1]['recorded_time_ns']
                    <= rejection['recorded_time_ns'] <= result['ended_time_ns']):
            raise ValueError('cannot correlate final separation stop with recorded feedback and hold')
        for dispatch, accepted in zip(dispatches, accepts):
            if not dispatch['recorded_time_ns'] < accepted['recorded_time_ns'] < rejection['recorded_time_ns']:
                raise ValueError('invalid observer dispatch/acceptance ordering')
    wires = list(source.glob('wire/*/*/calls.jsonl'))
    if len(wires) != 1:
        raise ValueError('expected one wire capture for interrupted motion')
    rows = [json.loads(line) for line in wires[0].read_text().splitlines()]
    row = rows[-1]
    output = row.get('response', {}).get('output', [])
    calls = [item for item in output if item.get('type') == 'function_call']
    round_trips = [e for e in events if e['kind'] == 'model_round_trip']
    attempts = round_trips[-1]['call'] if round_trips else len(rows)
    if ((round_trips and len(round_trips) != len(rows))
            or attempts != saved['http_attempts'] or result.get('http_attempts') != attempts
            or row.get('status') != 200 or len(calls) != 1 or calls[0]['name'] != 'move_joints'
            or any(item.get('type') not in {'reasoning', 'message', 'function_call'} for item in output)):
        raise ValueError('cannot establish final model move response')
    request = restore_blobs(row['request'], wires[0].parent.parent / 'blobs')
    from inspect_robots_agent._responses import _translate_messages
    if request['input'] != _translate_messages(saved['messages'], saved['raw_items'], cache_anchors=False):
        raise ValueError('wire request does not match checkpoint history')
    call = calls[0]
    if separation_stop:
        # Require an actual prefix of the saved model's interpolated move,
        # rather than accepting an unrelated series of small joint commands.
        labels = [f'{side}_j{i}' for side in ('left', 'right') for i in range(6)] + ['right_gripper']
        targets = json.loads(call['arguments'])['targets']
        if not targets or set(targets) - set(labels):
            raise ValueError('invalid observer move dimensions in wire response')
        start = np.asarray(saved['target'], dtype=float)
        end = start.copy()
        for label, value in targets.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value):
                raise ValueError('invalid observer wire target')
            end[labels.index(label)] = value
        delta = end - start
        count = int(np.ceil(np.max(np.abs(delta) / step_limits)))
        if (not len(accepts) < count or np.any(np.abs(delta[:-1]) > .80 + 1e-8)
                or not 0 <= end[-1] <= .90):
            raise ValueError('observer recovery requires a bounded, partially executed move')
        for index, accepted in enumerate(accepts, 1):
            if not np.allclose(np.asarray(accepted['applied'])[indices], start + delta * index / count, atol=1e-8, rtol=0):
                raise ValueError('observer journal is not a prefix of the interrupted model move')
    state = copy.deepcopy(saved)
    recovery_reason = ('Tracking guard stopped the run: accepted target was not reached. The daemon now holds the recorded measured pose. Do not retry the unreachable target.'
                       if tracking_stop else 'Wall-clock limit interrupted this move. Only part executed.')
    if rejected_motion:
        recovery_reason = 'Waypoint rejected before dispatch. No part of this tool call executed. Reassess fresh observations before choosing a new action.'
    if separation_stop:
        recovery_reason = ('Inter-arm interpolation certificate stopped this move after partial execution. '
                           'The daemon confirmed hold. No collision is inferred from this certificate failure; '
                           'reassess clearance in fresh views. Keep the held gripper command unchanged.')
    state['messages'].extend([
        dict(role='assistant', content=None, tool_calls=[dict(id=call['call_id'], type='function',
            function=dict(name=call['name'], arguments=call['arguments']))]),
        dict(role='tool', tool_call_id=call['call_id'], content=json.dumps(dict(
            interrupted=True, reason=recovery_reason,
            last_accepted_step=last['step'], last_accepted_target=previous[indices].tolist(),
            instruction='Do not replay or complete the old trajectory. Reassess fresh images and feedback.'))),
        dict(role='user', content='Recovery boundary: prior move was interrupted; await fresh live observations.')])
    state['raw_items'][call['call_id']] = output
    state.update(phase='awaiting_model', step=last['step'], target=(measured if tracking_stop else previous[indices]).tolist(),
                 measured=measured.tolist(), rechecks=0,
                 motion_recovery=dict(original_step=saved['step'], recovered_step=last['step'],
                                      interrupted_call_id=call['call_id'], tracking_stop=tracking_stop, rejected_before_motion=rejected_motion,
                                      last_accepted_target=previous[indices].tolist()))
    if separation_stop:
        # SAFE HOLD captures measured arm positions, not the unfinished
        # trajectory's setpoints. Anchor the new planner there; retain the
        # last accepted closure command so the object grip is not opened.
        state['target'] = measured[:-1].tolist() + [float(previous[-1])]
        state['motion_recovery'].update(separation_stop=True, accepted_waypoints=len(accepts),
            interrupted_move_waypoints=count, reference='recorded_held_arm_pose_with_accepted_gripper',
            restored_target=state['target'])
    return state


def load_resume(source, *, recover_interrupted_motion=False, max_total_steps=MAX_STEPS, recover_tracking_stop=False,
                recover_rejected_motion=False, recover_separation_stop=False):
    if type(max_total_steps) is not int or max_total_steps < 1:
        raise ValueError('max_total_steps must be a positive integer')
    source = Path(source).resolve()
    checkpoint = source / 'checkpoint.json'
    if recover_separation_stop and (recover_interrupted_motion or recover_tracking_stop or recover_rejected_motion or not checkpoint.exists()):
        raise ValueError('separation recovery requires its own checkpoint and no other recovery mode')
    if checkpoint.exists():
        state = json.loads(checkpoint.read_text())
        if recover_separation_stop and (state.get('control_mode') != 'active_perception' or state.get('phase') != 'dispatching_or_finished'):
            raise ValueError('separation recovery requires an interrupted observer move')
        if state.get('control_mode') == 'active_perception' and state.get('phase') != 'awaiting_model' and not recover_separation_stop:
            raise ValueError('observer motion interruption requires review; right-only recovery is unavailable')
        if (recover_interrupted_motion or recover_tracking_stop or recover_rejected_motion or recover_separation_stop) and state.get('phase') == 'dispatching_or_finished':
            state = recover_motion_boundary(source, state, recover_tracking_stop=recover_tracking_stop,
                recover_rejected_motion=recover_rejected_motion, recover_separation_stop=recover_separation_stop)
        if state.get('version') != 1 or state.get('phase') != 'awaiting_model':
            raise ValueError('interruption was outside a model boundary; motion outcome needs review')
    else:
        # Legacy recovery is deliberately restricted to a failed final request,
        # with journal proof that no command was accepted after its start.
        files = list(source.glob('wire/*/*/calls.jsonl'))
        if len(files) != 1:
            raise ValueError('expected one legacy wire capture')
        rows = [json.loads(line) for line in files[0].read_text().splitlines()]
        last = rows[-1]
        if last['status'] is not None and last['status'] != 429 and last['status'] < 500:
            raise ValueError('last request did not fail transiently; cannot infer a safe recovery point')
        events = [json.loads(line) for line in (source / 'station-events.jsonl').read_text().splitlines()]
        commands = [e for e in events if e['kind'] == 'command_acceptance']
        decisions = [e for e in events if e['kind'] == 'decision_observation']
        dispatches = [e for e in events if e['kind'] in ('command_dispatch', 'command_acceptance')]
        if not commands or not decisions or any(e['recorded_time_ns'] >= int(last['t'] * 1e9) for e in dispatches):
            raise ValueError('cannot establish a stationary unanswered model boundary')
        request = restore_blobs(last['request'], files[0].parent.parent / 'blobs')
        messages, raw = canonical_history(request['input'])
        target = commands[-1]['applied']
        if (commands[-1]['step'] != decisions[-1]['step']
                or not np.array_equal(target[7:], decisions[-1]['target'])):
            raise ValueError('last accepted command does not match saved decision boundary')
        rechecks = 0
        for item in reversed(request['input']):
            if item.get('type') == 'function_call':
                if item['name'] == 'move_joints':
                    break
                if item['name'] == 'observation_recheck':
                    rechecks += 1
        state = dict(version=1, phase='awaiting_model', messages=messages, raw_items=raw,
            calls_used=len({r['call'] for r in rows}), http_attempts=len(rows),
            step=decisions[-1]['step'], initial=target, target=decisions[-1]['target'],
            measured=decisions[-1]['measured_joints'], rechecks=min(rechecks, 2), legacy=True)
    # Validate before any station connection or session activation.
    dim = 13 if state.get('control_mode') == 'active_perception' else 7
    for key, shape in [('initial', (14,)), ('target', (dim,)), ('measured', (dim,))]:
        vector = np.asarray(state[key], dtype=float)
        if vector.shape != shape or not np.all(np.isfinite(vector)):
            raise ValueError(f'invalid saved {key}')
    if not state['messages'] or state['messages'][-1]['role'] != 'user':
        raise ValueError('checkpoint lacks unanswered observation')
    if (not 0 <= state['step'] < max_total_steps or not 0 <= state['calls_used'] < MAX_CALLS
            or not 0 <= state['http_attempts'] < MAX_CALLS):
        raise ValueError('saved experiment budget exhausted')
    state['source'] = str(source)
    return state


def validate_observer_guard(state, current):
    """Only explicit separation-stop recovery may adopt the revised certificate.

    Registered transforms/hash, distance threshold, margin and coverage must
    match exactly. The original checkpoint is never modified on disk.
    """
    saved = state.get('observer_guard')
    if saved == current:
        return
    old = dict(saved or {})
    new = dict(current)
    method = new.pop('certificate_method', None)
    if (not state.get('motion_recovery', {}).get('separation_stop')
            or 'certificate_method' in old or old != new
            or method != 'adaptive_two_endpoint_lipschitz_v1'
            or new.get('mode') != 'registered_fk_wrist_gripper'
            or new.get('motion_ready') is not True or new.get('metric_separation_verified') is not True
            or not new.get('configuration_sha256')):
        raise ValueError('observer resume geometry/guard mismatch; preserve the original reviewed configuration')
    state['motion_recovery']['guard_certificate_upgrade'] = dict(previous=copy.deepcopy(saved), current=copy.deepcopy(current))
    state['observer_guard'] = copy.deepcopy(current)


def validate_pose(state, measured_full):
    """No repositioning: require both arms to match the saved boundary."""
    current = np.asarray(measured_full, dtype=float)
    if current.shape != (14,) or not np.all(np.isfinite(current)):
        raise ValueError('invalid live joint feedback')
    if state.get('control_mode') == 'active_perception':
        indices = np.r_[0:6,7:14]
        measured = np.asarray(state['measured'], dtype=float)
        target = np.asarray(state['target'], dtype=float)
        locked = abs(current[6] - state['initial'][6])
        difference = float(np.max(np.abs(current[indices] - measured)))
        # Compare all observed dimensions for pose continuity, but apply the
        # arm tracking threshold only to arm joints: a grasp can hold g > 0
        # while its unchanged closure command is g = 0.
        residual = float(np.max(np.abs(current[indices][:12] - target[:12])))
        if locked > .05 or difference > .008 or residual > .08:
            raise ValueError('observer resume pose mismatch; no motion started')
        return dict(observer_pose_difference=difference, locked_gripper_difference=float(locked), target_residual=residual)
    left = float(np.max(np.abs(current[:7] - state['initial'][:7])))
    right = float(np.max(np.abs(current[7:] - state['measured'])))
    residual = float(np.max(np.abs(current[7:] - state['target'])))
    if left > .008 or right > .008 or residual > .08:
        raise ValueError(f'resume pose mismatch: left={left:.5f}, right={right:.5f}, target residual={residual:.5f}; no motion started')
    return dict(left_difference=left, right_difference=right, target_residual=residual)


def install_checkpoint(policy, arm, output, transport):
    complete = policy._client.complete
    path = Path(output) / 'checkpoint.json'
    def checkpointed(messages, tools, **kwargs):
        arm.check()
        state = dict(version=1, phase='awaiting_model', saved_time_ns=time.time_ns(),
            messages=messages, raw_items=policy._client._raw_items_by_call_id,
            calls_used=policy._calls_used + 1, http_attempts=transport.calls,
            step=arm.t, initial=arm.initial.tolist(), target=arm.last.tolist(),
            measured=np.asarray(arm.measured_feedback()['position']).tolist(), rechecks=arm.rechecks)
        if getattr(arm, 'active_perception', False) or arm.task_id == 'plug-socket':
            state['control_mode'] = arm.control_mode
            state['observer_guard'] = arm.separation_guard.describe() if arm.active_perception else None
        if arm.task_id == 'plug-socket':
            state['mask_left_view'] = arm.mask_left_view
        atomic_json(path, state)
        def attempted(calls):
            state['http_attempts'] = calls
            atomic_json(path, state)
        transport.attempt_sink = attempted
        try:
            response = complete(messages, tools, **kwargs)
        finally:
            transport.attempt_sink = None
        arm.check_model_wait()
        # Invalidate BEFORE any tool is executed or motion can be dispatched.
        # If interrupted here, refuse automated resume rather than duplicate it.
        state['phase'] = 'dispatching_or_finished'
        atomic_json(path, state)
        return response
    policy._client.complete = checkpointed
