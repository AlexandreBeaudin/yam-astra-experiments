"""Actual local IK/planner + upstream adapter; synthetic station I/O only."""
import json
import unittest
from unittest.mock import patch

import numpy as np
import astra_run as run
from inspect_robots import Scene
from inspect_robots.policy_adapter import ToolAdapter
from planned_motion import PosePlanner, goal_tools, trajectory
from test_astra_run import make_arm


def bound_adapter(arm):
    adapter = ToolAdapter(pre_check=arm.pre_check)
    adapter.bind(arm.info)
    adapter.add_tools(goal_tools(arm))
    return adapter


class PlannedMotionTests(unittest.TestCase):
    def test_j5_is_flange_roll_about_grasp_approach_axis(self):
        planner = PosePlanner()
        # Arbitrary model poses: no scene waypoint or hardware connection.
        for q in (np.array([.2, .4, .5, -.3, .1, -.2, 1.]),
                  np.array([-.3, .8, .6, -.5, -.1, .4, .5])):
            p0, r0 = planner.fk(q)
            turned = q.copy()
            turned[5] += .3
            p1, r1 = planner.fk(turned)
            np.testing.assert_allclose(p1, p0, atol=1e-8)
            np.testing.assert_allclose(r1[:, 2], r0[:, 2], atol=1e-8)
            self.assertAlmostEqual(float(r0[:, 0] @ r1[:, 0]), float(np.cos(.3)), places=7)

    def test_real_ik_and_adapter_cross_workspace_in_one_call(self):
        arm, robot = make_arm()
        observation = arm.reset(Scene(id='offline', instruction='Synthetic pose goal'))
        planner = arm.pose_planner = PosePlanner()
        arm.pose_inputs_available = True
        q = arm.last.copy()
        q[:6] = [.55, .35, .4, .15, -.25, .1]
        position, rotation = planner.fk(q)
        seen = []
        def synthetic_clearance(path):
            self.assertFalse(path.flags.writeable)
            seen.append(path.copy())
            return True
        arm.path_clearance_check = synthetic_clearance
        adapter = bound_adapter(arm)
        result = adapter.tools.execute('move_gripper_to_pose', json.dumps(dict(
            frame='right_base', position_m=position.tolist(), rotation_matrix=rotation.tolist(),
            note='Synthetic reachable pose, not a measured above-block target.')), observation)
        self.assertIsNone(result.error, result.error)
        actions = result.chunk.actions
        path = np.vstack([arm.last] + [a.data for a in actions])
        self.assertEqual(len(seen), 1)
        self.assertGreater(np.max(np.abs(path[-1, :6]-path[0, :6])), .15)
        self.assertLessEqual(np.max(np.abs(np.diff(path[:, :6], axis=0))), .003+1e-9)
        # Include rest before/after when checking speed and acceleration.
        velocity = np.diff(np.vstack([path[0], path, path[-1]]), axis=0)*30
        self.assertLessEqual(np.max(np.abs(velocity[:, :6])), .09+1e-8)
        self.assertLessEqual(np.max(np.abs(np.diff(velocity[:, :6], axis=0)*30)), .09+1e-6)
        p, r = planner.fk(path[-1])
        self.assertLess(np.linalg.norm(p-position), .0005)
        self.assertLess(np.linalg.norm(r-rotation), .002)
        np.testing.assert_array_equal(path[:, 6], np.ones(len(path)))
        # Real dispatch adapter with fake RPC, including unchanged left target.
        with patch.object(run.time, 'sleep'):
            for action in actions:
                arm.step(action)
        self.assertEqual(len(robot.commands), len(actions))
        self.assertFalse(arm._approved_path)
        self.assertTrue(all(np.array_equal(c[:7], arm.initial[:7]) for c in robot.commands))
        refreshed = arm.decision_observation(observation)
        self.assertEqual(set(refreshed.images), {'main', 'left', 'right', 'main_comparison'})
        np.testing.assert_array_equal(refreshed.state['joint_pos'], path[-1])
        # Adapter held target follows contributed tool, so sleep cannot snap back.
        hold = adapter.tools.execute('sleep', json.dumps({'seconds': .1, 'note': 'hold'}), refreshed)
        np.testing.assert_allclose(hold.chunk.actions[-1].data, path[-1])

    def test_actual_agent_gets_fresh_views_between_goal_actions(self):
        import httpx
        from pathlib import Path
        import tempfile
        from demo import offline_only
        arm, robot = make_arm()
        planner = arm.pose_planner = PosePlanner()
        arm.pose_inputs_available = True
        q = np.array([.3, .2, .3, .1, -.2, .1, 1.])
        position, rotation = planner.fk(q)
        arm.path_clearance_check = lambda path: True  # Explicit synthetic fixture.
        calls = []
        def respond(request):
            body = json.loads(request.content)
            calls.append(body)
            names = {t['name'] for t in body['tools']}
            self.assertTrue({'move_gripper_to_pose', 'set_gripper'} <= names)
            if len(calls) == 1:
                name = 'move_gripper_to_pose'
                args = dict(frame='right_base', position_m=position.tolist(),
                            rotation_matrix=rotation.tolist(), note='Synthetic pose goal')
            else:
                self.assertTrue(robot.commands)
                self.assertGreater(json.dumps(body).count('input_image'), 3)
                self.assertIn('Measured change since previous decision', json.dumps(body))
                if len(calls) == 2:
                    self.assertGreater(abs(robot.joints[7]), .15)
                    name, args = 'set_gripper', dict(opening=0., note='Synthetic closure')
                else:
                    self.assertEqual(robot.joints[13], 0.)
                    name, args = 'give_up', dict(reason='Offline contract complete', hindsight='Synthetic scene only')
            return httpx.Response(200, json={'status': 'completed', 'output': [dict(
                type='function_call', call_id=str(len(calls)), name=name, arguments=json.dumps(args))]})
        policy = run.make_policy(run.GatewayTransport(httpx.MockTransport(respond)),
            arm.pre_check, station=arm, env={'RHOS_API_KEY': 'offline-placeholder'})
        with tempfile.TemporaryDirectory() as output, offline_only(), patch.object(run.time, 'sleep'):
            log = run.execute(policy, arm, Path(output))
        self.assertEqual(log.status, 'success')
        self.assertEqual(len(calls), 3)
        decisions = [e for e in arm.events if e['kind'] == 'decision_observation']
        self.assertEqual(len(decisions), 3)
        self.assertTrue(decisions[0]['step'] < decisions[1]['step'] < decisions[2]['step'])

    def test_missing_and_rejected_checker_fail_closed(self):
        arm, robot = make_arm()
        arm.reset(Scene(id='blocked', instruction='Observe'))
        target = arm.last.copy()
        target[0] = .5
        points = trajectory(arm.last, target)
        self.assertIn('missing trusted swept-path', arm.pre_check(points, planned=True))
        for verdict in (False, None):
            arm.path_clearance_check = lambda path: verdict
            self.assertIn('rejected or could not validate', arm.pre_check(points, planned=True))
            self.assertFalse(arm._approved_path)
        self.assertFalse(robot.commands)

    def test_full_gripper_goal_and_checked_path_identity(self):
        arm, robot = make_arm()
        observation = arm.reset(Scene(id='gripper', instruction='Close'))
        arm.path_clearance_check = lambda path: True
        adapter = bound_adapter(arm)
        result = adapter.tools.execute('set_gripper', json.dumps({'opening': 0., 'note': 'close'}), observation)
        self.assertIsNone(result.error)
        path = np.stack([a.data for a in result.chunk.actions])
        np.testing.assert_array_equal(path[:, :6], np.zeros((len(path), 6)))
        self.assertEqual(path[-1, 6], 0.)
        changed = result.chunk.actions[0].data.copy()
        changed[0] += 1e-6
        with self.assertRaisesRegex(run.SafetyAbort, 'differs from clearance-checked'):
            arm.step(run.Action(data=changed))
        self.assertFalse(robot.commands)

    def test_bounds_tracking_and_stale_feedback_still_stop(self):
        arm, robot = make_arm()
        arm.reset(Scene(id='guards', instruction='Observe'))
        arm.path_clearance_check = lambda path: True
        bad = arm.last.copy()
        bad[0] = 3.01
        with self.assertRaises(run.SafetyAbort):
            arm.pre_check(trajectory(arm.last, bad), planned=True)
        robot.joints[7] = -.081
        target = arm.last.copy()
        target[0] = .5
        self.assertIn('behind its setpoint', arm.pre_check(trajectory(arm.last, target), planned=True))
        self.assertFalse(arm._approved_path)
        robot.stale = True
        with self.assertRaisesRegex(RuntimeError, 'stale'):
            arm.pre_check(trajectory(arm.last, target), planned=True)
        self.assertFalse(robot.commands)


if __name__ == '__main__':
    unittest.main()
