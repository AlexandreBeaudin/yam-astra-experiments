"""Regression coverage for startup sagging; fake motors only, no CAN or SDK."""

import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from i2rt.motor_drivers.dm_driver import MotorCmd
from i2rt.robots import get_robot, utils
from i2rt.robots import motor_chain_robot


class FakeChain:
    instances = []

    def __init__(self, motor_list=None, motor_offset=None, motor_direction=None, channel='test', **kwargs):
        self.motor_list = motor_list if motor_list is not None else [(i, 'DM4310') for i in range(7)]
        count = len(self.motor_list)
        self.motor_offset = np.array(motor_offset if motor_offset is not None else [0.] * count)
        self.motor_direction = motor_direction if motor_direction is not None else [1] * count
        self.channel = channel
        self.pos = np.zeros(count)
        self.command_lock = threading.RLock()
        self.commands = [MotorCmd() for _ in range(count)]
        self.sent = []
        self.drift = False
        self.rolled_back = False
        self.started = False
        self.instances.append(self)

    def read_states(self):
        return [SimpleNamespace(pos=float(p-o), eff=0.) for p, o in zip(self.pos, self.motor_offset)]

    def __len__(self):
        return len(self.pos)

    def set_commands(self, torques, pos=None, vel=None, kp=None, kd=None):
        # A torque-only call deliberately clears PD, just like the real driver.
        size = len(self.pos)
        arrays = [np.zeros(size) if value is None else np.array(value, copy=True)
                  for value in (pos, vel, kp, kd)]
        self.commands = [MotorCmd(pos=arrays[0][i], vel=arrays[1][i], kp=arrays[2][i],
                                  kd=arrays[3][i], torque=torques[i]) for i in range(size)]
        self.sent.append(self.commands.copy())
        self.pos[-1] = np.clip(self.pos[-1] + np.sign(torques[-1]) * .1, -.3, .3)
        if self.drift:
            self.pos[1] = .2

    def start_thread(self):
        assert all(cmd.kp > 0 for cmd in self.commands[:-1]), 'arm unheld at first control cycle'
        self.started = True

    def rollback_initialization(self):
        self.rolled_back = True


class StartupHoldingTests(unittest.TestCase):
    def setUp(self):
        self.chain = FakeChain()
        self.kp = np.array([80.] * 6 + [20.])
        self.kd = np.array([5.] * 6 + [1.])
        self.limits = np.array([[-3., 3.]] * 6)

    def hold(self):
        utils.hold_startup_position(self.chain, self.kp, self.kd, self.limits, gripper_index=6)

    def test_both_chains_hold_before_either_thread_starts(self):
        other = FakeChain(channel='right')
        for chain in (self.chain, other):
            utils.hold_startup_position(chain, self.kp, self.kd, self.limits, gripper_index=6)
        for chain in (self.chain, other):
            chain.start_thread()
            self.assertEqual(chain.commands[-1].kp, 0.)
            np.testing.assert_equal([c.pos for c in chain.commands], chain.pos)

    def test_calibration_preserves_arm_hold_and_removes_test_torque(self):
        self.chain.pos[1] = .4
        self.hold()
        with patch.object(utils.time, 'sleep'):
            high, low = utils.detect_gripper_limits(self.chain, 6)
        self.assertGreater(high, low)
        for commands in self.chain.sent:
            self.assertTrue(all(c.kp == 80. and c.kd == 5. for c in commands[:6]))
            self.assertEqual(commands[1].pos, .4)
        self.assertEqual(self.chain.commands[-1].torque, 0.)

    def test_calibration_rejects_missing_arm_hold(self):
        with self.assertRaisesRegex(RuntimeError, 'position hold is required'):
            utils.detect_gripper_limits(self.chain, 6)
        self.assertEqual(self.chain.sent, [])

    def test_drift_aborts_without_recapturing_moved_arm(self):
        self.hold()
        self.chain.drift = True
        with patch.object(utils.time, 'sleep'), self.assertRaisesRegex(RuntimeError, 'Arm moved'):
            utils.detect_gripper_limits(self.chain, 6)
        self.assertEqual(self.chain.commands[1].pos, 0.)
        self.assertEqual(self.chain.commands[-1].torque, 0.)

    def test_invalid_pose_and_feedback_rejected_before_commands(self):
        for value in (4., float('nan')):
            self.chain.pos[1] = value
            with self.assertRaises((ValueError, RuntimeError)):
                self.hold()
        self.assertEqual(self.chain.sent, [])

    def test_factory_queues_hold_after_wrap_correction_before_start(self):
        class WrappedChain(FakeChain):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.pos[0] = 3.4

        with patch.object(get_robot, 'DMChainCanInterface', WrappedChain), \
                patch.object(get_robot, 'combine_arm_and_gripper_xml', return_value='unused.xml'), \
                patch.object(get_robot, '_load_joint_limits_from_xml', return_value=self.limits.copy()):
            prepared = get_robot.prepare_yam_robot('test', zero_gravity_mode=False)
        chain = prepared.motor_chain
        self.assertFalse(chain.started)
        self.assertAlmostEqual(chain.commands[0].pos, 3.4 - 2 * np.pi)
        prepared.start_control()
        self.assertTrue(chain.started)

    def test_invalid_startup_pose_rolls_back_enabled_chain(self):
        class InvalidChain(FakeChain):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.pos[1] = -.26

        limits = self.limits.copy()
        limits[1, 0] = 0.  # factory adds its existing -0.15 buffer
        with patch.object(get_robot, 'DMChainCanInterface', InvalidChain), \
                patch.object(get_robot, 'combine_arm_and_gripper_xml', return_value='unused.xml'), \
                patch.object(get_robot, '_load_joint_limits_from_xml', return_value=limits):
            with self.assertRaisesRegex(RuntimeError, 'outside joint limits'):
                get_robot.prepare_yam_robot('test', zero_gravity_mode=False)
        chain = FakeChain.instances[-1]
        self.assertTrue(chain.rolled_back)
        self.assertFalse(chain.started)

    def test_logged_left_pose_uses_existing_initialization_tolerance(self):
        class LoggedPoseChain(FakeChain):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.pos[1] = -.15468833447775943

        limits = self.limits.copy()
        limits[1, 0] = 0.
        with patch.object(get_robot, 'DMChainCanInterface', LoggedPoseChain), \
                patch.object(get_robot, 'combine_arm_and_gripper_xml', return_value='unused.xml'), \
                patch.object(get_robot, '_load_joint_limits_from_xml', return_value=limits):
            prepared = get_robot.prepare_yam_robot('test', zero_gravity_mode=False)
        self.assertFalse(prepared.motor_chain.rolled_back)
        self.assertAlmostEqual(prepared.motor_chain.commands[1].pos, -.15468833447775943)
        self.assertGreater(prepared.motor_chain.commands[1].kp, 0.)

    def test_drift_while_waiting_for_other_arm_aborts_before_thread_start(self):
        self.hold()
        prepared = get_robot.PreparedYAMRobot(
            self.chain, lambda: None, 'test', startup_arm_position=np.zeros(6),
        )
        self.chain.pos[1] = .1
        with self.assertRaisesRegex(RuntimeError, 'before control thread'):
            prepared.start_control()
        self.assertTrue(self.chain.rolled_back)
        self.assertFalse(self.chain.started)

    def test_position_hold_is_installed_before_first_robot_update(self):
        chain = FakeChain(motor_list=[(i, 'DM4310') for i in range(6)])
        chain.pos[1] = .4
        first_commands = []

        class CheckThread:
            def __init__(self, target, **kwargs):
                self.target = target

            def start(self):
                robot = self.target.__self__
                first_commands.append(robot._commands)

        with patch.object(motor_chain_robot.threading, 'Thread', CheckThread), \
                patch.object(motor_chain_robot.time, 'sleep'), \
                patch.object(motor_chain_robot.MotorChainRobot, '_motor_state_to_joint_state',
                             return_value=SimpleNamespace(pos=chain.pos.copy())):
            motor_chain_robot.MotorChainRobot(
                chain, use_gravity_comp=False, gripper_index=None,
                joint_limits=self.limits, zero_gravity_mode=False,
                kp=self.kp[:6], kd=self.kd[:6],
            )
        self.assertEqual(len(first_commands), 1)
        np.testing.assert_array_equal(first_commands[0].kp, self.kp[:6])
        np.testing.assert_array_equal(first_commands[0].pos, chain.pos)


if __name__ == '__main__':
    unittest.main()
