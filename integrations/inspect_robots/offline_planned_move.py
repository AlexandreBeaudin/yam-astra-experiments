"""Reproduce the real pose-tool boundary offline. No station/model connections."""
import json
import numpy as np

import astra_run as run
from inspect_robots import Observation
from inspect_robots.policy_adapter import ToolAdapter
from offline_run007_approach import SavedArm, SavedRobot
from planned_motion import PosePlanner, goal_tools


def verify():
    start = np.array([0., 0., 0., 0., 0., 0., 1.])
    initial = np.r_[start, start]
    arm = SavedArm(SavedRobot(initial), None, {'joint_limits': [[-3., 3.]]*14}, simulated=True)
    arm.last, arm.initial, arm.saved_measured = start, initial, start.copy()
    planner = arm.pose_planner = PosePlanner()
    candidate = start.copy()
    candidate[:6] = [.55, .35, .4, .15, -.25, .1]
    p, r = planner.fk(candidate)
    args = json.dumps(dict(frame='right_base', position_m=p.tolist(),
        rotation_matrix=r.tolist(), note='Synthetic reachable pose; actual above-block pose unavailable.'))
    adapter = ToolAdapter(pre_check=arm.pre_check)
    adapter.bind(arm.info)
    adapter.add_tools(goal_tools(arm))
    obs = Observation(state={'joint_pos': start})
    assert 'move_gripper_to_pose' not in {tool.name for tool in adapter.tools}
    unavailable = 'pose tool omitted: missing grounded inputs and trusted swept-path checker'
    seen = []
    def synthetic_checker(path):
        seen.append(path.copy())
        return True
    arm.path_clearance_check = synthetic_checker
    arm.pose_inputs_available = True  # Explicit synthetic grounded target above.
    adapter.add_tools(goal_tools(arm))
    result = adapter.tools.execute('move_gripper_to_pose', args, obs)
    assert result.error is None, result.error
    path = np.vstack([start] + [a.data for a in result.chunk.actions])
    p1, r1 = planner.fk(path[-1])
    delta = float(np.max(np.abs(path[-1, :6]-start[:6])))
    assert delta > .15
    assert len(seen) == 1
    assert np.linalg.norm(p1-p) < .0005
    return dict(trial_launched=False, station_connections=0, hardware_dispatches=0,
        astra_requests=0, actual_above_block_validated=False,
        missing_pieces=[
            'Grounded pink-block and paper-X poses/table plane in right_base, with grasp_site-to-physical-gripper registration; no camera-to-base transform or equivalent measured target is supplied.',
            'Trusted continuous swept-path checker covering table, objects, fixtures, stationary left arm, gripper/cables and tracking uncertainty; main() installs no path_clearance_check.'],
        current_setup_rejection=unavailable,
        synthetic_contract=dict(goal_calls=1, clearance='synthetic True fixture, not physical validation',
            planner='actual local YAM Pro/linear-4310 MuJoCo IK and quintic trajectory',
            adapter='actual upstream ToolAdapter + contributed goal tool + StationArm.pre_check',
            start=start.tolist(), target_position_m=p.tolist(), target_rotation_matrix=r.tolist(),
            waypoints=len(path)-1, max_total_joint_displacement_rad=delta,
            max_joint_step_rad=float(np.max(np.abs(np.diff(path[:, :6], axis=0)))),
            endpoint_position_error_m=float(np.linalg.norm(p1-p)),
            endpoint_rotation_matrix_error=float(np.linalg.norm(r1-r))))


if __name__ == '__main__':
    from demo import offline_only
    with offline_only():
        print(json.dumps(verify(), indent=2))
