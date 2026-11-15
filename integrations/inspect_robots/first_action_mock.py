#!/usr/bin/env python3
"""Replay saved Trial 005 to a closed mock boundary; no executable primitive."""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import sys

from demo import HERE, WORKSPACE, offline_only, sha

sys.path.insert(0, str(WORKSPACE / 'yam3_trial_replay'))
from policy_evidence import response_evidence
from execution_timeline import export_frame_video

TRIAL = WORKSPACE / 'gpt_policy_trial_005'
PINS = {
    'response.real.json': 'd8264f843573b72a5bc72a2a630bdd57632b6edb66e67b59eb23825fd0963940',
    'manifest.json': '3b33d924680c1187835919c622e17029f01ae65e4fefc2069a7c85d49bb3c73e',
    'dry_run.py': '954e60388ebdb74a080ca5f2a5ab6b79a160a1d68610ae4fcb8df06923a94902',
    'decisions.jsonl': 'bf15e67f526cfb16efb471ba8217efc6ce725ab12deec32c8f6e9bd64b94f0cb',
}


class ClosedMockController:
    """Deny semantic dispatch. No robot interface, joint targets or device handles."""
    def review(self, proposal, validation):
        return {'accepted': False, 'submitted': False, 'primitive': None,
                'reason': 'no_lab_reviewed_right_arm_primitive',
                'semantic_valid': validation['valid']}


def replay():
    with offline_only():
        raw = {name: (TRIAL / name).read_bytes() for name in PINS}
        for name, expected in PINS.items():
            if sha(raw[name]) != expected:
                raise ValueError('fixture changed: ' + name)
        spec = importlib.util.spec_from_file_location('trial005_validator', TRIAL / 'dry_run.py')
        validator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(validator)
        manifest = validator.strict_json(raw['manifest.json'])
        image = manifest['images'][0]
        image_path = (TRIAL / image['path']).resolve()
        if TRIAL not in image_path.parents or sha(image_path.read_bytes()) != image['sha256']:
            raise ValueError('saved image mismatch')
        response = validator.strict_json(raw['response.real.json'])
        proposal = validator.parse_response(response)
        validation = validator.validate(proposal, manifest)
        journal = [validator.strict_json(line) for line in raw['decisions.jsonl'].splitlines() if line.strip()]
        saved = [row for row in journal if row.get('response_id') == response['id']]
        if len(saved) != 1 or saved[0]['proposed_action'] != proposal or saved[0]['validation'] != validation:
            raise ValueError('response does not match saved validated decision')
        if response['model'] != 'gpt-6-astra' or not validation['valid']:
            raise ValueError('expected saved validated Astra decision')
        if manifest['execution_enabled'] is not False or validation['motion_permitted'] is not False:
            raise ValueError('offline permission contract changed')
        common = {'decision_id': response['id'], 'mock_only': True,
                  'clock': 'synthetic_event_order_not_station_time'}
        payloads = [
            ('proposed_action', {'proposal': proposal, 'source': 'saved_astra_response',
                'policy_evidence': response_evidence(response),
                'fixture_sha256': PINS, 'image_sha256': image['sha256'],
                'observation_status': 'saved_not_fresh'}),
            ('validation', {'result': validation}),
            ('command_acceptance', ClosedMockController().review(proposal, validation)),
            ('measured_completion', {'status': 'not_available', 'completed': None,
                'measurements': [], 'reason': 'no_command_submitted_no_hardware_observation',
                'physical_task_success': 'unverified'}),
        ]
        return [dict(common, event_id=f'trial005-boundary-{i}', sequence=i,
                     event=kind, **data) for i, (kind, data) in enumerate(payloads)]


def run(output):
    output = Path(output).resolve()
    if HERE not in output.parents or output == HERE / 'vendor' or HERE / 'vendor' in output.parents:
        raise ValueError('output must be new and inside integration, outside vendor')
    events = replay()
    output.mkdir(parents=True, exist_ok=False)
    (output / 'events.jsonl').write_text(''.join(json.dumps(e, sort_keys=True) + '\n' for e in events))
    summary = {'result': 'blocked_at_mock_boundary', 'event_count': len(events),
               'api_calls': 0, 'robot_connections': 0, 'robot_commands': 0,
               'primitive': None, 'execution_enabled': False,
               'measured_completion': 'not_available', 'physical_task_success': 'unverified'}
    (output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    export_view(output, events)
    return summary


def export_view(output, events):
    """One saved response, then causal validation/acceptance/completion stages."""
    manifest = json.loads((TRIAL / 'manifest.json').read_text())
    image = manifest['images'][0]
    shutil.copyfile(TRIAL / image['path'], output / 'saved-frame.jpg')
    policy = {**events[0]['policy_evidence'], 'proposal': events[0]['proposal'],
              'event_id': events[0]['event_id']}
    execution, snapshots = {}, []
    for i, event in enumerate(events):
        if event['event'] == 'validation':
            policy['validation'] = {'event_id': event['event_id'], **event['result']}
        elif event['event'] == 'command_acceptance':
            execution['acceptance'] = event
        elif event['event'] == 'measured_completion':
            execution['completion'] = event
        snapshots.append(json.loads(json.dumps({
            'trial_id': 'trial005-saved-astra-boundary', 'time_s': float(i),
            'receipt_time_ns': str(i * 1_000_000_000), 'clock_domain': event['clock'],
            'stage': event['event'], 'image': 'saved-frame.jpg',
            'policy_evidence': policy, 'execution_evidence': execution,
            'observation_evidence': {'image_sha256': image['sha256'],
                'status': 'SAVED FRAME; acquisition freshness not established',
                'physical_outcome': 'UNVERIFIED', 'criterion': manifest['success_criterion']},
        })))
    view_manifest = {'trial_id': snapshots[0]['trial_id'], 'duration_s': 3.,
                     'presentation_fps': 1, 'clock': 'synthetic ordering; not station time',
                     'source': 'hash-pinned Trial 005 saved response; closed mock controller',
                     'source_sha256': PINS, 'physical_outcome': 'UNVERIFIED'}
    export_frame_video(output, view_manifest, snapshots)
    template = (WORKSPACE / 'yam3_trial_replay/replay_template.html').read_text()
    template = template.replace('Saved recording · outcome unverified', 'Saved Astra response · CLOSED MOCK execution gate')
    template = template.replace('Astra decisions: <strong>MISSING</strong>. No synthetic evidence.',
        'Real saved Astra proposal; validation replayed offline. Stage order is synthetic.')
    template = template.replace('alt="Recorded cameras, missing Astra decision evidence, requested and accepted commands, measured robot telemetry and outcome evidence"',
        'alt="Saved Trial 005 camera frame; no new acquisition"')
    start, end = template.index('<footer>'), template.index('</footer>')
    template = template[:start] + '<footer>Saved image, synthetic event order, no hardware commands. Scrub to distinguish proposal, validation, blocked acceptance and unavailable measured completion.' + template[end:]
    payload = json.dumps({'manifest': view_manifest, 'snapshots': snapshots}).replace('<', '\\u003c')
    (output / 'replay.html').write_text(template.replace('__REPLAY_DATA__', payload))
    (output / 'manifest.json').write_text(json.dumps(view_manifest, indent=2) + '\n')
    (output / 'timeline.json').write_text(json.dumps(snapshots, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    print(json.dumps(run(parser.parse_args().output), indent=2))
