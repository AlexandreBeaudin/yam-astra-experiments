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


def load_resume(source):
    source = Path(source).resolve()
    checkpoint = source / 'checkpoint.json'
    if checkpoint.exists():
        state = json.loads(checkpoint.read_text())
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
    for key, shape in [('initial', (14,)), ('target', (7,)), ('measured', (7,))]:
        vector = np.asarray(state[key], dtype=float)
        if vector.shape != shape or not np.all(np.isfinite(vector)):
            raise ValueError(f'invalid saved {key}')
    if not state['messages'] or state['messages'][-1]['role'] != 'user':
        raise ValueError('checkpoint lacks unanswered observation')
    if (not 0 <= state['step'] < 5400 or not 0 <= state['calls_used'] < 180
            or not 0 <= state['http_attempts'] < 180):
        raise ValueError('saved experiment budget exhausted')
    state['source'] = str(source)
    return state


def validate_pose(state, measured_full):
    """No repositioning: require both arms to match the saved boundary."""
    current = np.asarray(measured_full, dtype=float)
    if current.shape != (14,) or not np.all(np.isfinite(current)):
        raise ValueError('invalid live joint feedback')
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
