"""Bridge canonical framework logs into the existing replay template, visibly mock."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

from PIL import Image, ImageDraw, ImageFont, ImageOps

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / 'vendor/src'))
from inspect_robots import read_eval_log
sys.path.insert(0, str(HERE.parent.parent / 'yam3_trial_replay'))
from execution_timeline import export_frame_video


def export_replay(log_path, output):
    log_path, output = Path(log_path), Path(output)
    log = read_eval_log(str(log_path))
    if (log.eval.embodiment != 'paper-x-saved-frame-mock-arm'
            or not log.samples[0].scene_metadata.get('mock_data')):
        raise ValueError('only this integration mock log is supported')
    output.mkdir(exist_ok=False)
    trial_id = log.samples[0].scene_id
    transitions = log.samples[0].trial_metadata[0]['paper_x_transitions']
    source_manifest = json.loads((log_path.parent / 'trial004.manifest.json').read_text())
    source = HERE.parent.parent / 'gpt_policy_trial_004' / source_manifest['images'][0]['path']
    if hashlib.sha256(source.read_bytes()).hexdigest() != log.samples[0].scene_metadata['saved_image_sha256']:
        raise ValueError('replay source image hash mismatch')
    events, snapshots = [], []
    for i, transition in enumerate(transitions):
        decision = transition['decision_id']
        event_id = f'{trial_id}:{decision}'
        # Simulation order only; never join this clock to the stationary60 MCAP.
        event = {'id': event_id, 'trial_id': trial_id, 'topic': '/mock/paper_x/decision',
                 'receipt_time_ns': str(i * 1_000_000_000),
                 'clock_domain': 'synthetic_mock_step', 'data': transition}
        events.append(event)
        snapshot = {'trial_id': trial_id, 'time_s': float(i),
            'receipt_time_ns': event['receipt_time_ns'], 'clock_domain': 'synthetic_mock_step',
            'image': f'step-{i:03d}.jpg', 'astra_decision': None,
            'simulated_decision': event_id,
            'input_observation': transition['input']['observation_id'],
            'next_observation': transition['next_input']['observation_id'],
            'mock_outcome': transition['mock_outcome'], 'mock_data': True,
            'physical_commands': 'MISSING: no robot connected',
            'physical_state': 'MISSING', 'outcome_evidence': 'UNVERIFIED'}
        snapshot['policy_evidence'] = {
            'source': 'simulated_fixture', 'decision_id': decision, 'event_id': event_id,
            'summary_status': 'SIMULATED', 'proposal': transition['proposal'],
            'validation': transition['validator'],
        }
        snapshot['execution_evidence'] = {
            'acceptance': 'NO PHYSICAL COMMAND: toy mock only',
            'completion': {'mock_outcome': transition['mock_outcome'],
                           'physical_completion': 'UNVERIFIED'},
        }
        snapshot['observation_evidence'] = {
            'input': transition['input'], 'next_input': transition['next_input'],
            'freshness': 'unchanged saved pixels; synthetic mock state changes only',
            'physical_outcome': 'UNVERIFIED',
        }
        snapshots.append(snapshot)
        canvas = Image.new('RGB', (1600, 1040), '#fffcf2')
        draw = ImageDraw.Draw(canvas)
        font = ImageFont.truetype('DejaVuSans.ttf', 18)
        title = ImageFont.truetype('DejaVuSans.ttf', 28)
        draw.text((30, 25), 'PAPER-X / INSPECT ROBOTS / OFFLINE MOCK', font=title, fill='#087f8c')
        draw.text((30, 80), 'Trial 004 saved frame: unchanged pixels, no fresh acquisition', font=font, fill='#403d39')
        with Image.open(source) as image:
            canvas.paste(ImageOps.contain(image.convert('RGB'), (760, 620)), (30, 160))
        details = {'decision_id': decision,
                   'input': transition['input']['mock_state'],
                   'proposal': transition['proposal'],
                   'validator_valid': transition['validator']['valid'],
                   'validator_errors': transition['validator']['errors'],
                   'mock_outcome': transition['mock_outcome'],
                   'next_input': transition['next_input']['mock_state']}
        # Short line wrapping keeps every proposal and validator field visible.
        import textwrap
        text = '\n'.join('\n'.join(textwrap.wrap(line, 65, replace_whitespace=False))
                         for line in json.dumps(details, indent=2).splitlines())
        draw.multiline_text((820, 120), text, font=font, spacing=3, fill='#252422')
        draw.text((30, 930), 'MOCK STATE ONLY | Astra API calls 0 | Robot connections 0 | Robot commands 0', font=font, fill='#087f8c')
        draw.text((30, 975), 'Physical task success UNVERIFIED. Synthetic clock; no MCAP alignment.', font=font, fill='#403d39')
        canvas.save(output / snapshot['image'])
    manifest = {'trial_id': trial_id, 'duration_s': float(max(0, len(snapshots)-1)),
                'presentation_fps': 1, 'mock_data': True,
                'framework_log_sha256': hashlib.sha256(log_path.read_bytes()).hexdigest(),
                'framework_revision': log.samples[0].scene_metadata['framework_revision'],
                'clock_domain': 'synthetic_mock_step', 'outcome': 'unverified',
                'missing': ['fresh post-release frame', 'physical commands', 'measured robot state'],
                'source': 'framework EvalLog.samples[0].trial_metadata[0].paper_x_transitions'}
    export_frame_video(output, manifest, snapshots)
    (output / 'events.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in events))
    (output / 'timeline.json').write_text(json.dumps(snapshots, indent=2) + '\n')
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    template = (HERE.parent.parent / 'yam3_trial_replay/replay_template.html').read_text()
    template = template.replace('Saved recording · outcome unverified', 'OFFLINE MOCK · physical outcome unverified')
    template = template.replace('Astra decisions: <strong>MISSING</strong>. No synthetic evidence.',
        'Simulated model decisions and mock outcomes; no Astra calls. Synthetic state is labeled.')
    template = template.replace('alt="Recorded cameras, missing Astra decision evidence, requested and accepted commands, measured robot telemetry and outcome evidence"',
        'alt="Saved Trial 004 frame with simulated proposal, validator result and mock outcome"')
    start, end = template.index('<footer>'), template.index('</footer>')
    template = template[:start] + '<footer>Mock steps use a synthetic clock. The saved camera frame never changes. Physical state and task success remain unverified.' + template[end:]
    template = template.replace('Each accepted command links to its recorded request by command ID. Decision links are null when missing.',
        'Simulated decisions link exactly to their input and next mock observations. No physical command links exist.')
    payload = json.dumps({'manifest': manifest, 'snapshots': snapshots}).replace('<', '\\u003c')
    (output / 'replay.html').write_text(template.replace('__REPLAY_DATA__', payload))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--log', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    resolved = args.output.resolve()
    if HERE not in resolved.parents or HERE / 'vendor' in resolved.parents:
        parser.error('output must be inside integration and outside vendor')
    export_replay(args.log, resolved)
