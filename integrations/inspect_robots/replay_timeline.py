"""Normalize recorded evidence for the hardware replay; never imports robot code.

All displayed times use the first camera receipt as origin. Historical tool
spans are inferred execution envelopes, not instrumented tool-handler timings.
"""
from __future__ import annotations
import json
import re


def build_timeline(data, *, title=None, harness_label='Inspect Robots', metadata=None, returns=None):
    metadata, returns = dict(metadata or {}), returns or {}
    for call in data.get('calls', []):
        response = call.get('response') or {}
        if response.get('model'):
            metadata.setdefault('model', response['model'])
        if response.get('reasoning', {}).get('effort'):
            metadata.setdefault('effort', response['reasoning']['effort'])
    events = sorted(data.get('events', []), key=lambda e: e.get('time_s', 0))
    frames = sorted(data.get('frames', []), key=lambda e: e.get('time_s', 0))
    duration = max([0] + [e.get('time_s', 0) for e in events + frames])
    by_hash = {f.get('sha256'): f.get('image') for f in frames}
    decisions = []
    for e in events:
        if e.get('kind') == 'decision_observation':
            decisions.append(dict(time_s=e['time_s'], step=e.get('step'), images={
                camera: by_hash.get(info.get('sha256')) for camera, info in e.get('cameras', {}).items()}))
    models = []
    for e in events:
        if e.get('kind') == 'model_round_trip':
            models.append(dict(start=max(0, e['time_s'] - e['duration_s']), end=e['time_s'],
                               call=e.get('call'), status=e.get('outcome'), wall_end_ns=e.get('recorded_time_ns'), timing='monotonic duration ending at journal receipt'))
    # Old replay data retains wall-clock wire calls. Use a paired journal clock
    # only when the monotonic transport journal is unavailable.
    anchor = next((e for e in events if e.get('recorded_time_ns')), None)
    if not models and anchor:
        for c in data.get('calls', []):
            if c.get('t') is not None and c.get('duration_s') is not None:
                start = c['t'] - anchor['recorded_time_ns'] / 1e9 + anchor['time_s']
                models.append(dict(start=max(0, start), end=start+c['duration_s'], call=c.get('call'), timing='wall clock mapped to journal'))
    tools, motion = [], []
    dispatch = {e['command_id']: e for e in events if e.get('kind') == 'command_dispatch'}
    for e in events:
        if e.get('kind') == 'motion_time':
            d = dispatch.get(e.get('command_id'))
            motion.append(dict(start=d['time_s'] if d else max(0, e['time_s']-e['duration_s']),
                               end=e['time_s'], step=e.get('step'), command_id=e.get('command_id')))
    # Pair wire completion with the transport's wall-clock end marker. This
    # tolerates missing/damaged wire rows without shifting subsequent tools.
    # Legacy fixtures without paired clocks fall back to recorded attempt order.
    calls = data.get('calls', [])
    for i, call in enumerate(calls):
        model_index = i
        if call.get('t') is not None and call.get('duration_s') is not None:
            candidates = [(abs(m['wall_end_ns']/1e9-call['t']-call['duration_s']), j)
                          for j, m in enumerate(models) if m.get('wall_end_ns')]
            if candidates:
                difference, model_index = min(candidates)
                if difference > 1:
                    continue  # Unmatched timing evidence must not acquire a false span.
        if model_index >= len(models):
            continue
        model = models[model_index]
        next_start = models[model_index+1]['start'] if model_index+1 < len(models) else duration
        boundary = next((d['time_s'] for d in decisions if d['time_s'] >= model['end']), next_start)
        response = call.get('response') or {}
        for item in response.get('output', []):
            if item.get('type') != 'function_call':
                continue
            try:
                args = json.loads(item.get('arguments', '{}'))
            except (ValueError, TypeError):
                args = {'raw_arguments': item.get('arguments')}
            if not isinstance(args, dict):
                args = {'arguments': args}
            steps = [m for m in motion if model['end'] <= m['start'] < next_start]
            observations = [e for e in events if e.get('kind') == 'observation' and
                            model['end'] <= e['time_s'] <= boundary]
            readback = observations[-1] if observations else None
            end = max([model['end']] + [m['end'] for m in steps] + ([readback['time_s']] if readback else []))
            if not steps:
                end = max(model['end'], boundary)
            returned = returns.get(item.get('call_id'), 'Return not recorded')
            planned = re.search(r'over (\d+) steps', str(returned))
            status = ('Partial execution' if planned and len(steps) < int(planned.group(1))
                      else 'Readback recorded' if steps and readback else 'Recorded return')
            tools.append(dict(start=model['end'], end=end, name=item.get('name', 'unknown'),
                call_id=item.get('call_id'), args=args, note=args.get('note') or args.get('reason') or '',
                returned=returned, status=status,
                steps=len(steps), motion_duration_s=sum(m['end']-m['start'] for m in steps),
                readback=(readback or {}).get('measured_joints'),
                readback_time_s=(readback or {}).get('time_s'), timing='inferred execution envelope'))
    goal = metadata.get('goal') or 'Recorded robot episode'
    if title is None:
        title = 'Pink block pick-and-place' if 'pink block' in goal.lower() else goal
    model_name = metadata.get('model', 'Unknown model')
    if model_name == 'gpt-6-astra':
        model_name = 'GPT-6 Astra'
    return dict(duration_s=duration, title=title, harness_label=harness_label,
                model=model_name, effort=metadata.get('effort', 'unknown'),
                main=[dict(time_s=f['time_s'], image=f.get('image')) for f in frames
                      if f.get('camera') == 'main' and f.get('source') == 'preview_recording'],
                decisions=decisions, models=models, tools=tools, motion=motion,
                timing_note='Receipt timestamps; exposure times unknown. Tools show inferred execution envelopes. Motion shows recorded dispatch-to-step-end intervals.')
