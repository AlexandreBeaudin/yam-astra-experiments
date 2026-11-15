import time
from typing import Any, Dict, Optional

import numpy as np

from gello.cameras.camera import CameraDriver
from gello.robots.robot import Robot


class Rate:
    def __init__(self, rate: float):
        self.rate = rate
        self.reset()

    def reset(self):
        self.last = time.monotonic()
        self.deadline = self.last + 1.0 / self.rate
        self.missed_deadlines = 0

    def sleep(self) -> None:
        now = time.monotonic()
        if now < self.deadline:
            time.sleep(self.deadline - now)
        now = time.monotonic()
        period = 1.0 / self.rate
        missed = max(0, int((now - self.deadline) / period))
        self.missed_deadlines += missed
        self.deadline += (missed + 1) * period
        self.last = now


class RobotEnv:
    def __init__(
        self,
        robot: Robot,
        control_rate_hz: float = 100.0,
        camera_dict: Optional[Dict[str, CameraDriver]] = None,
    ) -> None:
        self.control_rate_hz = control_rate_hz
        self._robot = robot
        self._rate = Rate(control_rate_hz)
        self._camera_dict = {} if camera_dict is None else camera_dict
        self.last_command_result = None
        self._num_dofs = robot.num_dofs()

    def robot(self) -> Robot:
        """Get the robot object.

        Returns:
            robot: the robot object.
        """
        return self._robot

    def __len__(self):
        return 0

    def step(self, joints: np.ndarray) -> Dict[str, Any]:
        """Step the environment forward.

        Args:
            joints: joint angles command to step the environment with.

        Returns:
            obs: observation from the environment.
        """
        if len(joints) != self._num_dofs:
            raise ValueError(f"input:{len(joints)}, robot:{self._num_dofs}")
        requested_time_ns = time.time_ns()
        self.last_command_result = self._robot.command_joint_state(joints)
        if isinstance(self.last_command_result, dict):
            self.last_command_result["requested_time_ns"] = requested_time_ns
        self._rate.sleep()
        if isinstance(self.last_command_result, dict):
            self.last_command_result["missed_control_periods"] = self._rate.missed_deadlines
        fused = (self.last_command_result.get("observation")
                 if isinstance(self.last_command_result, dict) else None)
        return self.get_obs(fused)

    def get_obs(self, robot_obs=None) -> Dict[str, Any]:
        """Get observation from the environment.

        Returns:
            obs: observation from the environment.
        """
        observations = {}
        for name, camera in self._camera_dict.items():
            image, depth = camera.read()
            observations[f"{name}_rgb"] = image
            observations[f"{name}_depth"] = depth

        if robot_obs is None:
            robot_obs = self._robot.get_observations()
        assert "joint_positions" in robot_obs
        assert "joint_velocities" in robot_obs
        assert "ee_pos_quat" in robot_obs
        observations["joint_positions"] = robot_obs["joint_positions"]
        observations["joint_velocities"] = robot_obs["joint_velocities"]
        if "joint_efforts" in robot_obs:
            observations["joint_efforts"] = robot_obs["joint_efforts"]
        observations["ee_pos_quat"] = robot_obs["ee_pos_quat"]
        observations["gripper_position"] = robot_obs["gripper_position"]
        return observations


def main() -> None:
    pass


if __name__ == "__main__":
    main()
