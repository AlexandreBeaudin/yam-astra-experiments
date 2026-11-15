"""Offline migration regression suite. Never opens robot/camera hardware.

Rebuilds the named demo runs; archive existing artifacts before the first run.
Each phase has a separate log and completion status, including actual failures.
"""
import argparse
import datetime
import json
from pathlib import Path
import os
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--from-phase', default='smoke')
a = p.parse_args()
out = ROOT / 'runs/isaacsim_5_1_migration'
out.mkdir(parents=True, exist_ok=True)
utility = str(ROOT / '.venv/bin/python')
source = str(ROOT.parent / 'dobot_xtrainer20250912/dobot_xtrainer')


def py(script, *args):
    return [utility, str(ROOT / 'scripts' / script), *args]


def sim(script, *args):
    return ['bash', str(ROOT / 'scripts/isaac_python.sh'),
            str(ROOT / 'scripts' / script), *args]


phases = [
    ('smoke', sim('smoke_isaac.py')),
    ('prepare_episode', py('prepare_episode.py', '--source-root', source)),
    ('build_scene', py('build_scene.py', '--source-root', source)),
    ('validate_scene', py('validate_scene.py')),
    ('projection', sim('check_isaac_projection.py')),
    ('visual_replay', sim('render_isaac.py', '--stride', '1')),
    ('visual_review', py('build_review.py')),
    ('build_contact', py('build_contact_scene.py')),
    ('contact_recorded', sim('run_contact_replay.py', '--frames', '410')),
    ('contact_disabled', sim('run_contact_replay.py', '--variant', 'no_finger_contact', '--frames', '410')),
    ('bench_240', sim('run_contact_replay.py', '--scenario', 'grasp_bench', '--frames', '180')),
    ('bench_480', sim('run_contact_replay.py', '--scenario', 'grasp_bench', '--physics-hz', '480', '--frames', '180')),
    ('bench_zero_friction', sim('run_contact_replay.py', '--scenario', 'grasp_bench', '--variant', 'zero_friction', '--frames', '180')),
    ('contact_evaluation', py('evaluate_contact.py')),
]
for variant in ('nominal', 'grasp_bench_nominal'):
    phases.extend([
        ('bake_' + variant, py('bake_contact_result.py', '--variant', variant)),
        ('render_' + variant, sim('render_isaac.py', '--run',
                                  str(ROOT / 'runs/stack_cups_contact' / variant), '--stride', '1')),
        ('review_' + variant, py('build_contact_review.py', '--variant', variant)),
    ])
phases.extend([
    ('contact_artifacts', py('check_contact_artifacts.py')),
    ('material_batch', sim('test_material_batch_isaac.py')),
    ('distribution', py('run_distribution_pipeline.py', '--count', '54')),
    ('goal_distribution', py('prepare_goal_supervision.py', '--count', '36')),
    ('goal_usd', py('export_goal_scenes.py')),
    ('unit_tests', [utility, '-m', 'unittest', 'discover', '-s', 'tests', '-v']),
    ('goal_browser', py('check_goal_supervision.py')),
    ('continuous_collector', py('check_collector_integration.py')),
    ('runtime_final_smoke', sim('smoke_isaac.py')),
    ('projection_final', sim('check_isaac_projection.py')),
    ('failure_propagation', py('check_isaac_failure.py')),
    ('acceptance', py('check_isaac_migration_results.py')),
])
names = [name for name, _ in phases]
if a.from_phase not in names:
    p.error('Unknown phase: ' + a.from_phase)
path = out / 'suite.json'
report = json.loads(path.read_text()) if path.exists() else {'phases': {}}
report.update(hardware_started=False, requested_simulator='5.1.0',
              expected_phases=names, passed=False)
path.write_text(json.dumps(report, indent=2))
env = dict(os.environ, PYTHONPATH=str(ROOT / 'src'))
for name, command in phases[names.index(a.from_phase):]:
    log = out / (name + '.log')
    if log.exists():
        archived = out / ('%s.attempt%d.log' % (name, len(list(out.glob(name + '.attempt*.log'))) + 1))
        log.rename(archived)
        if name in report['phases']:
            previous = dict(report['phases'][name], phase=name, log=str(archived.relative_to(ROOT)))
            report.setdefault('history', []).append(previous)
    started = time.monotonic()
    print('START', name, flush=True)
    with log.open('w') as stream:
        try:
            result = subprocess.run(command, cwd=ROOT, env=env,
                                    stdout=stream, stderr=subprocess.STDOUT,
                                    timeout=1800)
            code = result.returncode
        except subprocess.TimeoutExpired:
            code = 124
    report['phases'][name] = {'command': command, 'exit_code': code,
                             'passed': code == 0, 'seconds': time.monotonic() - started,
                             'completed_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                             'log': str(log.relative_to(ROOT))}
    path.write_text(json.dumps(report, indent=2))
    print('PASS' if code == 0 else 'FAIL', name, flush=True)
    if code:
        raise SystemExit('Phase failed: %s; inspect %s' % (name, log))
report['passed'] = all(report['phases'].get(n, {}).get('passed') for n in names)
path.write_text(json.dumps(report, indent=2))
print('OFFLINE_SUITE_PASSED', report['passed'], flush=True)
