"""Run 007 replay through the real planner/guards; no network or dispatch.

The injected clear/blocked verdicts test the integration contract, NOT physical
clearance. All joint phases use the current per-call ceiling.
"""
import hashlib
import json
import re
from pathlib import Path
from unittest.mock import patch

import astra_run as run
import numpy as np
from PIL import Image
from demo import offline_only
from inspect_robots.policy_adapter import ToolAdapter

ARCHIVE = run.HERE / 'astra-yam3-run-007'
OUTPUT = run.HERE / 'offline-review/run-007-approach-cap'


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def archive_manifest():
    files = sorted(p for p in ARCHIVE.rglob('*') if p.is_file())
    files.append(ARCHIVE.with_suffix('.zip'))
    return {str(p.relative_to(run.HERE)): digest(p) for p in files}


class SavedRobot:
    def __init__(self, initial):
        self.joints = initial.copy()

    def get_joint_state(self):
        return self.joints.copy()

    def command_joint_state(self, target):
        raise AssertionError('DISPATCH DISABLED')


class SavedArm(run.StationArm):
    def measured_feedback(self):
        # Offline replay of the saved readback. No invented live freshness.
        return dict(position=self.saved_measured.copy(), source_time_ns=0)


def replay_inputs():
    with (ARCHIVE / 'station-events.jsonl').open() as stream:
        events = [json.loads(line) for line in stream]
    decisions = [e for e in events if e['kind'] == 'decision_observation']
    first_dispatch = next(e for e in events if e['kind'] == 'command_dispatch')
    framework = json.loads((ARCHIVE / 'framework-log.json').read_text())
    # Bounds exactly as supplied to Astra in this run, not a widened test box.
    def strings(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for v in value.values():
                yield from strings(v)
        elif isinstance(value, list):
            for v in value:
                yield from strings(v)
    text = next(s for s in strings(framework) if 'Per-dimension bounds: right_j0:' in s)
    bounds = [list(map(float, re.search(re.escape(label) + r': \[([^,]+), ([^\]]+)\]', text).groups()))
              for label in run.LABELS]
    return decisions, np.asarray(first_dispatch['target'][:7]), bounds


def scenario(decision, left, bounds, delta, phase, verdict):
    initial = np.concatenate((left, decision['target']))
    robot = SavedRobot(initial)
    seen = []

    def checker(path):
        assert not path.flags.writeable
        np.testing.assert_array_equal(path[0], decision['target'])
        seen.append(path.copy())
        return verdict

    arm = SavedArm(robot, None, {'joint_limits': bounds + bounds}, simulated=True,
                   path_clearance_check=None if verdict == 'absent' else checker)
    arm.initial, arm.last = initial, np.asarray(decision['target']).copy()
    arm.saved_measured = np.asarray(decision['measured_joints'])
    adapter = ToolAdapter(pre_check=arm.pre_check, max_speed_frac=.1)
    adapter.bind(arm.info)
    library = run.station_motion_tools(adapter.tools, arm)
    images = {}
    for view in ('main', 'right'):
        meta = decision['cameras'][view]
        path = ARCHIVE / 'replay-frames' / (meta['sha256'] + '.jpg')
        assert digest(path) == meta['sha256']
        with Image.open(path) as image:
            images[view] = np.asarray(image.convert('RGB'))
    observation = run.Observation(images=images, state={'joint_pos': arm.saved_measured},
        extra={**decision, 'guard_reference': arm.last.tolist(), 'env_step': decision['step']})
    target = arm.last[0] + delta
    result = library.execute('move_joints', json.dumps(dict(
        targets={'right_j0': target}, phase=phase,
        note='Offline boundary candidate; saved images do not verify physical clearance.')), observation)
    actions = result.chunk.actions if result.chunk else []
    guard = run.GuardChain(run.motion_guard(arm.space), run.PauseOnIntervention(arm))
    store = {}
    guard.review(run.Action(data=arm.last.copy()), store)
    previous = arm.last.copy()
    for action in actions:
        assert guard.review(action, store) is action
        assert np.all(np.abs(action.data - previous) <= np.array([.003]*6 + [.02]) + 1e-8)
        previous = action.data
    allowance = next((e for e in reversed(arm.events) if e['kind'] == 'move_allowance'), {})
    if actions and arm._approved_path:
        assert len(arm._approved_path) == len(actions)
        # Dispatch rejects a changed waypoint before the disabled RPC.
        changed = actions[0].data.copy()
        changed[0] += .00001
        try:
            arm.step(run.Action(data=changed))
        except run.SafetyAbort:
            pass
        else:
            raise AssertionError('changed clearance-approved path was accepted')
    return dict(step=decision['step'], phase=phase, requested_delta_rad=delta,
                start_j0_rad=float(arm.initial[7]), requested_targets={'right_j0': float(target)},
                clearance_fixture=verdict, effective_cap_rad=allowance.get('cap_rad'),
                accepted=result.error is None, error=result.error, waypoints=len(actions),
                checker_calls=len(seen), dispatches=0, astra_calls=0)


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    before = archive_manifest()
    with offline_only(), patch.object(run.StationArm, 'observe', side_effect=AssertionError('no live observation')):
        decisions, left, bounds = replay_inputs()
        cases = {}
        for name, decision, delta, phase, verdict in [
            ('open_space_contract', decisions[0], .30, 'open_space_approach', True),
            ('run007_unknown_clearance', decisions[0], .30, 'open_space_approach', 'absent'),
            ('final_approach_large', decisions[-1], .20, 'alignment', True),
            ('final_approach_fine', decisions[-1], .02, 'alignment', True),
            ('unsafe_path', decisions[0], .20, 'open_space_approach', False),
            ('over_candidate_cap', decisions[0], .45001, 'open_space_approach', True),
            ('out_of_bounds', decisions[0], 4., 'open_space_approach', True),
        ]:
            cases[name] = scenario(decision, left, bounds, delta, phase, verdict)
        assert cases['open_space_contract']['accepted']
        assert cases['open_space_contract']['effective_cap_rad'] == .45
        assert cases['final_approach_large']['effective_cap_rad'] == .45
        assert cases['final_approach_large']['accepted']
        assert cases['run007_unknown_clearance']['accepted']
        assert cases['final_approach_fine']['accepted']
        for name in ('unsafe_path',
                     'over_candidate_cap', 'out_of_bounds'):
            assert not cases[name]['accepted'], name
    assert archive_manifest() == before, 'Run 007 archive changed'
    (OUTPUT / 'archive-sha256.json').write_text(json.dumps(before, indent=2) + '\n')
    report = dict(candidate_cap_rad=.45, saved_evidence_effective_cap_rad=.45,
                  physical_clearance_verified=False, archive_unchanged=True,
                  dispatches=0, astra_calls=0, cases=cases)
    (OUTPUT / 'results.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
