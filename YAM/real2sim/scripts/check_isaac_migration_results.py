"""Assert task-level migration invariants and compare archived 4.5 evidence."""
import json
from pathlib import Path
import cv2
import numpy as np
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]
out = ROOT / 'runs/isaacsim_5_1_migration'
baseline = out / 'baseline_4_5'
report = {'passed': False, 'scope': 'offline software regression; not physical twin certification'}


def read(path):
    return json.loads(path.read_text())


def version(data):
    assert Version(data['runtime']['version']) == Version('5.1.0')


projection = read(out / 'projection/report.json')
version(projection)
assert projection['passed'] and len(projection['checks']) == 45
report['projection_max_error_px'] = projection['max_error_px']
ev = read(ROOT / 'runs/stack_cups_contact/contact_evaluation.json')
old = read(baseline / 'stack_cups_contact/contact_evaluation.json')
for name in ('no_contact_control_passed', 'bench_240hz_lift_hold_passed',
             'bench_480hz_lift_hold_passed', 'zero_friction_bench_no_lift'):
    assert ev['checks'][name], name
assert ev['checks']['bench_peak_lift_difference_240_480_m'] < .005
report['contact_checks'] = ev['checks']
report['old_contact_checks'] = old['checks']
report['contact_comparison'] = {}
for name, run in ev['runs'].items():
    version(run)
    assert run['object_pose_writes_after_initialization'] == 0
    assert run['attachment_joints'] == 0
    old_tracks = np.load(baseline / 'stack_cups_contact' / name / 'physics_tracks.npz')
    new_tracks = np.load(ROOT / 'runs/stack_cups_contact' / name / 'physics_tracks.npz')
    link_names = {'J%d_%d' % (arm, link) for arm in (1, 2) for link in range(1, 7)}
    links = [k for k in old_tracks.files if k.rsplit('/', 1)[-1] in link_names]
    assert len(links) == 12
    motion_error = max(float(np.max(np.abs(old_tracks[k] - new_tracks[k]))) for k in links)
    assert motion_error < 1e-9, (name, motion_error)
    report['contact_comparison'][name] = {
        'old_cup2_max_lift_m': old['runs'][name]['cups']['2']['max_lift_m'],
        'new_cup2_max_lift_m': run['cups']['2']['max_lift_m'],
        'contact_events': run['physics_contact_reports'],
        'robot_motion_matrix_max_absolute_difference': motion_error,
    }
report['render_comparison'] = {}
for relative, expected in [('stack_cups', 410), ('stack_cups_contact/nominal', 410),
                           ('stack_cups_contact/grasp_bench_nominal', 180)]:
    run = ROOT / 'runs' / relative
    rendered = read(run / 'render_manifest.json')
    old_frames = set(read(baseline / relative / 'render_manifest.json')['frames'])
    version(rendered)
    assert rendered['frames'] == list(range(expected)), relative
    entries = {}
    for camera in ('top', 'left', 'right'):
        diffs = []
        for frame in range(expected):
            image = cv2.imread(str(run / 'renders' / camera / ('%04d.png' % frame)))
            assert image is not None and image.shape == (480, 640, 3) and image.std() > 1
            old_path = baseline / relative / 'renders' / camera / ('%04d.png' % frame)
            if frame in old_frames and old_path.exists():
                old_image = cv2.imread(str(old_path))
                diffs.append(float(np.abs(image.astype(float)-old_image.astype(float)).mean()))
        entries[camera] = {'valid_frames': expected, 'old_new_compared_frames': len(diffs),
                           'mean_absolute_rgb_difference': float(np.mean(diffs)) if diffs else None}
    report['render_comparison'][relative] = entries
layouts = read(ROOT / 'runs/stack_cups_distribution/layouts.json')
assert len(layouts['layouts']) == 54
assert all(n > 0 for n in layouts['guidance_coverage_counts'])
for layout in layouts['layouts']:
    version(layout['render_visibility'])
    expected_ready = bool(layout['validation']['passed'] and layout['static_physics']['passed']
                          and layout['render_visibility']['passed'])
    assert layout['guidance_ready'] == expected_ready, layout['id']
report['distribution'] = {'count': 54, 'ready': sum(l['guidance_ready'] for l in layouts['layouts']),
                          'coverage': layouts['guidance_coverage_counts']}
materials = read(ROOT / 'runs/material_reconstruction_batch/batch.json')
version(materials)
assert materials['assets']
assert all(a['static_physics']['passed'] for a in materials['assets'])
report['material_count'] = len(materials['assets'])
collector = read(ROOT / 'runs/task_x_0001_supervision/collector_integration_test.json')
for key in ('three_episodes_one_session', 'projection_frozen_at_start', 'repeat_same_guidance',
            'projection_bytes_preserved', 'first_frame_overlay', 'previous_next_buttons',
            'manual_marking_removed', 'stale_camera_rejected'):
    assert collector[key], key
assert collector['hardware_started'] is False
report['collector'] = collector
report['limitations'] = [
    'Existing estimated geometry, contact parameters, and failed stack/release are not fixed by an SDK upgrade.',
    'Collector test uses historical images and simulated record events; no live hardware session.',
    'YAM scene and live acceptance must be completed on YAM with the same simulator version.',
    'Pixel differences across renderers are reported, not treated as physical or photometric accuracy.',
]
report['passed'] = True
(out / 'acceptance.json').write_text(json.dumps(report, indent=2))
print(json.dumps(report, indent=2))
