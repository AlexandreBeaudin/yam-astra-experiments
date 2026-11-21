"""Per-waypoint tracking stops with synthetic RPC; no hardware or network."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

import astra_run as run
import httpx
from demo import offline_only
from inspect_robots import Action, Scene
from test_astra_run import FakePreview, FakeRobot, make_arm


class StalledWrist(FakeRobot):
    def command_joint_state(self, target):
        acceptance = super().command_joint_state(target)
        # The daemon accepts commands while the wrist cannot move past this
        # synthetic obstruction. Acceptance is not measured completion.
        self.joints[10] = max(self.joints[10], -.009)
        return acceptance


class WaypointTrackingTests(unittest.TestCase):
    def setUp(self):
        network = offline_only()
        network.__enter__()
        self.addCleanup(network.close)

    def test_each_arm_joint_and_both_error_signs_stop_before_dispatch(self):
        for joint in range(6):
            for sign in (-1, 1):
                with self.subTest(joint=joint, sign=sign):
                    arm, robot = make_arm()
                    arm.reset(Scene(id='tracking', instruction='Synthetic test'))
                    reference = arm.last.copy()
                    robot.joints[7 + joint] += sign * .080001
                    target = reference.copy()
                    target[joint] += sign * .001
                    with self.assertRaisesRegex(RuntimeError, 'before waypoint dispatch'):
                        arm.step(Action(data=target))
                    self.assertFalse(robot.commands)
                    self.assertEqual(arm.t, 0)
                    np.testing.assert_array_equal(arm.last, reference)
                    self.assertFalse(any(e['kind'] == 'command_dispatch' for e in arm.events))
                    event = arm.events[-1]
                    self.assertEqual(event['phase'], 'waypoint_tracking')
                    self.assertEqual(event['joint'], run.LABELS[joint])
                    self.assertEqual(event['blocked_step'], 1)
                    self.assertEqual(event['limit_rad'], .08)
                    self.assertGreater(event['residual_rad'], .08)
                    np.testing.assert_array_equal(event['reference_target'], reference)
                    np.testing.assert_array_equal(event['proposed_target'], target)

    def test_existing_boundary_allows_error_at_or_below_point_zero_eight(self):
        for error in (-.08, -.079, 0., .079, .08):
            with self.subTest(error=error):
                arm, robot = make_arm()
                observation = arm.reset(Scene(id='boundary', instruction='Synthetic test'))
                robot.joints[7] = error
                target = arm.last.copy()
                target[0] += .001
                with patch.object(run.time, 'sleep'), patch.object(arm, 'observe', return_value=observation):
                    arm.step(Action(data=target))
                self.assertEqual(len(robot.commands), 1)

    def test_compares_with_last_accepted_target_not_next_waypoint(self):
        arm, robot = make_arm()
        arm.reset(Scene(id='reference', instruction='Synthetic test'))
        robot.joints[7] = .0805
        target = arm.last.copy()
        target[0] = .003  # Error to proposed point is only .0775.
        with self.assertRaisesRegex(RuntimeError, 'before waypoint dispatch'):
            arm.step(Action(data=target))
        self.assertFalse(robot.commands)

    def test_stalled_wrist_aborts_remaining_chunk_without_another_rpc(self):
        robot = StalledWrist()
        arm = run.StationArm(robot, FakePreview(robot),
            {'joint_limits': [[-3., 3.]] * 14}, simulated=True)
        observation = arm.reset(Scene(id='stalled', instruction='Synthetic test'))
        points = [arm.last.copy() for _ in range(100)]
        for i, point in enumerate(points, 1):
            point[3] = -.003 * i
        arm._approved_path = [point.copy() for point in points]
        with patch.object(run.time, 'sleep'), patch.object(arm, 'observe', return_value=observation):
            with self.assertRaisesRegex(RuntimeError, 'before waypoint dispatch'):
                for point in points:
                    arm.step(Action(data=point))
        self.assertEqual(len(robot.commands), 30)
        self.assertEqual(arm.t, 30)
        self.assertAlmostEqual(arm.last[3], -.09)
        self.assertAlmostEqual(robot.joints[10], -.009)
        self.assertFalse(arm._approved_path)
        event = arm.events[-1]
        self.assertEqual(event['blocked_step'], 31)
        self.assertAlmostEqual(event['residual_rad'], .081)
        self.assertEqual(sum(e['kind'] == 'command_dispatch' for e in arm.events), 30)
        self.assertEqual(sum(e['kind'] == 'command_acceptance' for e in arm.events), 30)

    def test_gripper_difference_does_not_change_existing_arm_only_threshold(self):
        arm, robot = make_arm()
        observation = arm.reset(Scene(id='gripper', instruction='Synthetic test'))
        robot.joints[13] = .2
        with patch.object(run.time, 'sleep'), patch.object(arm, 'observe', return_value=observation):
            arm.step(Action(data=arm.last.copy()))
        self.assertEqual(len(robot.commands), 1)
        np.testing.assert_array_equal(robot.commands[0][:7], arm.initial[:7])

    def test_real_policy_loop_stops_chunk_and_preserves_failure_journal(self):
        robot = StalledWrist()
        arm = run.StationArm(robot, FakePreview(robot),
            {'joint_limits': [[-3., 3.]] * 14}, simulated=True, task_id='usb-insertion')
        spec = run.task_configuration('usb-insertion')
        calls = []

        def respond(request):
            calls.append(request)
            return httpx.Response(200, json={'status': 'completed', 'output': [{
                'type': 'function_call', 'call_id': 'synthetic-stall', 'name': 'move_joints',
                'arguments': json.dumps({'targets': {'right_j3': -.3},
                    'phase': 'alignment', 'note': 'Synthetic stalled wrist; no physical motion.'})}]})

        policy = run.make_policy(run.GatewayTransport(httpx.MockTransport(respond)),
            arm.pre_check, station=arm, prompt_path=spec['prompt'],
            env={'RHOS_API_KEY': 'offline-placeholder'})
        with tempfile.TemporaryDirectory() as temp, patch.object(run.time, 'sleep'), \
                patch('sys.stdout', io.StringIO()):
            log = run.execute(policy, arm, Path(temp), task_spec=spec)
            events = [json.loads(line) for line in (Path(temp) / 'station-events.jsonl').read_text().splitlines()]
        self.assertNotEqual(log.status, 'success')
        self.assertIn('before waypoint dispatch', json.dumps(log.to_dict()))
        self.assertEqual(len(calls), 1)
        stop = next(e for e in events if e.get('phase') == 'waypoint_tracking')
        dispatches = [e for e in events if e['kind'] == 'command_dispatch']
        self.assertLess(len(dispatches), 100)
        self.assertEqual(len(dispatches), len(robot.commands))
        self.assertEqual(dispatches[-1]['step'], stop['step'])
        self.assertFalse(any(e['step'] >= stop['blocked_step'] for e in dispatches))


if __name__ == '__main__':
    unittest.main()
