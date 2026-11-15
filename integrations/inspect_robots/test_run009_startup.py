"""Capability-gated startup and saved Run 009 request; no real I/O."""
import itertools
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

import httpx
import astra_run as run
from demo import offline_only
from offline_run009_startup import replay
from planned_motion import goal_tools
from test_astra_run import make_arm


class Run009StartupTests(unittest.TestCase):
    def test_each_pose_prerequisite_is_required_for_offer_and_recommendation(self):
        with offline_only():
            for inputs, planner, checker in itertools.product((False, True), repeat=3):
                with self.subTest(inputs=inputs, planner=planner, checker=checker):
                    arm, robot = make_arm()
                    arm.pose_inputs_available = inputs
                    arm.pose_planner = Mock() if planner else None
                    arm.path_clearance_check = (lambda path: True) if checker else None
                    policy = run.make_policy(httpx.MockTransport(lambda req: self.fail('No inference expected')), arm.pre_check, station=arm,
                                             env={'RHOS_API_KEY': 'offline-placeholder'})
                    policy.bind(arm.info)
                    names = {t.name for t in policy._adapter.tools}
                    ready = inputs and planner and checker
                    self.assertEqual('move_gripper_to_pose' in names, ready)
                    self.assertEqual('set_gripper' in names, checker)
                    self.assertIn('move_joints', names)
                    instructions = policy._adapter.instructions()
                    self.assertEqual('Use move_gripper_to_pose' in instructions, ready)
                    self.assertEqual('Control mode: move_joints' in instructions, not ready)
                    self.assertIn('Use fresh observations after each move', instructions)
                    self.assertFalse(robot.commands)

    def test_revoked_pose_inputs_reject_even_previously_offered_tool(self):
        arm, robot = make_arm()
        arm.reset(run.Scene(id='offline', instruction='Observe'))
        arm.pose_inputs_available = True
        arm.pose_planner = Mock()
        arm.path_clearance_check = lambda path: True
        library = goal_tools(arm)
        arm.pose_inputs_available = False
        result = library.execute('move_gripper_to_pose', json.dumps({'note': 'offline'}), arm.observe())
        self.assertIn('pose prerequisites unavailable', result.error)
        arm.pose_planner.solve.assert_not_called()
        self.assertFalse(robot.commands)

    def test_saved_startup_and_all_phase_cap_boundaries(self):
        with tempfile.TemporaryDirectory() as temp:
            result = replay(Path(temp))
            self.assertEqual(result['tools'], ['move_joints', 'done', 'give_up', 'sleep', 'observation_recheck'])
            self.assertEqual(len(result['cap_checks']), 24)
            self.assertEqual(result['robot_dispatches'], 0)
            self.assertFalse(result['trial_launched'])
            instructions = (Path(temp) / 'instructions.txt').read_text()
            self.assertNotIn('Use move_gripper_to_pose', instructions)
            self.assertNotIn('legacy diagnostic', instructions)
            self.assertIn('Control mode: move_joints', instructions)
            self.assertIn('Effective cap: 0.8', (Path(temp) / 'observation-report.txt').read_text())


if __name__ == '__main__':
    unittest.main()
