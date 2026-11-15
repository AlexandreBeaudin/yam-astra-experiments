"""Read saved evidence only. No clients, station checks, or archive writes."""
import json
from pathlib import Path
import numpy as np
import astra_run as run
from inspect_robots import Action
from inspect_robots.guard import _LAST_APPROVED_KEY


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def transition():
    root = run.HERE / 'astra-yam3-run-003'
    record = json.loads(next((root / 'records').rglob('record.json')).read_text())
    events = rows(root / 'station-events.jsonl')
    approvals = [e for e in record['events'] if e['kind'] == 'approval']
    actions = {e['t']: e['action'] for e in rows(next((root / 'actions').rglob('*.jsonl'))) if 't' in e}
    decision = next(e for e in events if e['kind'] == 'decision_observation' and e['step'] == 119)
    # Exact recorded bounds are in the framework record.
    space = run.Box((7,), low=np.array([-2.768, -.15, -.15, -1.843, -1.721, -2.244, 0]),
                    high=np.array([3.292, 3.815, 3.292, 1.721, 1.721, 2.244, 1]),
                    semantics=run.ActionSemantics(control_mode='joint_pos', gripper='continuous',
                        dim_labels=run.LABELS, max_step=(.003,) * 6 + (.02,)))
    guard, store = run.motion_guard(space), {}
    result = []
    for t in sorted(actions):
        approval = next((e for e in approvals if e['t'] == t), None)
        proposed = approval['data']['guards'][0]['before'] if approval else actions[t]
        reference = store.get(_LAST_APPROVED_KEY)
        action = Action(data=np.asarray(proposed))
        reviewed = guard.review(action, store)
        np.testing.assert_array_equal(reviewed.data, actions[t])
        if approval:
            dispatch = next(e for e in events if e['kind'] == 'command_dispatch' and e['step'] == t + 1)
            ack = next(e for e in events if e['kind'] == 'command_acceptance' and e['step'] == t + 1)
            measured = next(e for e in events if e['kind'] == 'observation' and e['step'] == t + 1)
            np.testing.assert_array_equal(reviewed.data, dispatch['target'][7:])
            np.testing.assert_array_equal(reviewed.data, ack['applied'][7:])
            result.append(dict(framework_step=t, station_step=t+1,
                planner_start_j2=decision['measured_joints'][2], guard_reference_j2=reference[2],
                proposed_j2=proposed[2], approved_j2=reviewed.data[2],
                dispatched_j2=dispatch['target'][9], measured_j2=measured['measured_joints'][2],
                measured_source_time_ns=measured['measured_source_time_ns'], station_limited=ack['limited']))
    assert [e['framework_step'] for e in result] == list(range(119, 124))
    return space, decision, np.asarray(actions[118]), result


if __name__ == '__main__':
    print(json.dumps(transition()[3], indent=2))
