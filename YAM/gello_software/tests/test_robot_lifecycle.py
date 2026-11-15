import unittest

import numpy as np

from gello.robots.robot import BimanualRobot
from gello.robots.yam import YAMRobot


class FakeHardwareRobot:
    def __init__(self):
        self.gravity_calls = 0
        self.close_args = []
        self.joints = __import__("numpy").arange(7, dtype=float)
        self.commands = []

    def enter_gravity_comp_idle(self):
        self.gravity_calls += 1

    def close(self, disable_motors=False):
        self.close_args.append(disable_motors)

    def get_joint_pos(self):
        return self.joints.copy()

    def command_joint_pos(self, target):
        self.commands.append(__import__("numpy").asarray(target).copy())


class RobotLifecycleTest(unittest.TestCase):
    def test_yam_and_bimanual_publish_actual_joint_limits(self):
        hardware = FakeHardwareRobot()
        hardware.get_robot_info = lambda: {
            "joint_limits": np.array([[-1.0, 1.0]] * 6)
        }
        left = YAMRobot._from_hardware_robot(hardware)
        right = YAMRobot._from_hardware_robot(hardware)

        expected_one = np.vstack((np.array([[-1.0, 1.0]] * 6), [0.0, 1.0]))
        np.testing.assert_array_equal(left.get_joint_limits(), expected_one)
        np.testing.assert_array_equal(
            BimanualRobot(left, right).get_joint_limits(),
            np.vstack((expected_one, expected_one)),
        )

    def test_yam_observations_use_actual_hardware_feedback(self):
        class ObservedHardware(FakeHardwareRobot):
            def get_observations(self):
                return {
                    "joint_pos": [1, 2, 3, 4, 5, 6],
                    "joint_vel": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6],
                    "joint_eff": [1.1, 1.2, 1.3, 1.4, 1.5, 1.6],
                    "gripper_pos": [0.75],
                    "gripper_vel": [0.05],
                    "gripper_eff": [0.25],
                }

        robot = YAMRobot._from_hardware_robot(ObservedHardware())

        observations = robot.get_observations()

        self.assertEqual(observations["joint_positions"].tolist(), [1, 2, 3, 4, 5, 6, 0.75])
        self.assertEqual(
            observations["joint_velocities"].tolist(),
            [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.05],
        )
        self.assertEqual(
            observations["joint_efforts"].tolist(),
            [1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 0.25],
        )
        self.assertEqual(observations["gripper_position"].tolist(), [0.75])

    def test_yam_forwards_gravity_idle_and_motor_disabling_close(self):
        hardware = FakeHardwareRobot()
        robot = YAMRobot._from_hardware_robot(hardware)

        robot.enter_gravity_comp_idle()
        robot.close(disable_motors=True)

        self.assertEqual(hardware.gravity_calls, 1)
        self.assertEqual(hardware.close_args, [True])

    def test_yam_safe_hold_captures_actual_feedback(self):
        hardware = FakeHardwareRobot()
        robot = YAMRobot._from_hardware_robot(hardware)

        captured = robot.enter_safe_hold()

        self.assertEqual(captured.tolist(), hardware.joints.tolist())
        self.assertEqual(hardware.commands[-1].tolist(), hardware.joints.tolist())

    def test_bimanual_forwards_lifecycle_to_both_arms(self):
        left = FakeHardwareRobot()
        right = FakeHardwareRobot()
        robot = BimanualRobot(left, right)

        robot.enter_gravity_comp_idle()
        robot.close(disable_motors=True)

        self.assertEqual(left.gravity_calls, 1)
        self.assertEqual(right.gravity_calls, 1)
        self.assertEqual(left.close_args, [True])
        self.assertEqual(right.close_args, [True])


if __name__ == "__main__":
    unittest.main()
