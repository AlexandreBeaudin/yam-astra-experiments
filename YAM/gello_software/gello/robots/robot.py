from abc import abstractmethod
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Protocol

import numpy as np


class Robot(Protocol):
    """Robot protocol.

    A protocol for a robot that can be controlled.
    """

    @abstractmethod
    def num_dofs(self) -> int:
        """Get the number of joints of the robot.

        Returns:
            int: The number of joints of the robot.
        """
        raise NotImplementedError

    @abstractmethod
    def get_joint_state(self) -> np.ndarray:
        """Get the current state of the leader robot.

        Returns:
            T: The current state of the leader robot.
        """
        raise NotImplementedError

    @abstractmethod
    def command_joint_state(self, joint_state: np.ndarray) -> None:
        """Command the leader robot to a given state.

        Args:
            joint_state (np.ndarray): The state to command the leader robot to.
        """
        raise NotImplementedError

    @abstractmethod
    def get_observations(self) -> Dict[str, np.ndarray]:
        """Get the current observations of the robot.

        This is to extract all the information that is available from the robot,
        such as joint positions, joint velocities, etc. This may also include
        information from additional sensors, such as cameras, force sensors, etc.

        Returns:
            Dict[str, np.ndarray]: A dictionary of observations.
        """
        raise NotImplementedError


class PrintRobot(Robot):
    """A robot that prints the commanded joint state."""

    def __init__(self, num_dofs: int, dont_print: bool = False):
        self._num_dofs = num_dofs
        self._joint_state = np.zeros(num_dofs)
        self._dont_print = dont_print

    def num_dofs(self) -> int:
        return self._num_dofs

    def get_joint_state(self) -> np.ndarray:
        return self._joint_state

    def command_joint_state(self, joint_state: np.ndarray) -> None:
        assert len(joint_state) == (self._num_dofs), (
            f"Expected joint state of length {self._num_dofs}, "
            f"got {len(joint_state)}."
        )
        self._joint_state = joint_state
        if not self._dont_print:
            print(self._joint_state)

    def get_observations(self) -> Dict[str, np.ndarray]:
        joint_state = self.get_joint_state()
        pos_quat = np.zeros(7)
        return {
            "joint_positions": joint_state,
            "joint_velocities": joint_state,
            "joint_efforts": np.zeros_like(joint_state),
            "ee_pos_quat": pos_quat,
            "gripper_position": np.array(0),
        }


class BimanualRobot(Robot):
    def __init__(self, robot_l: Robot, robot_r: Robot):
        self._robot_l = robot_l
        self._robot_r = robot_r
        self._command_pool = ThreadPoolExecutor(max_workers=2)

    def num_dofs(self) -> int:
        return self._robot_l.num_dofs() + self._robot_r.num_dofs()

    def get_joint_state(self) -> np.ndarray:
        return np.concatenate(
            (self._robot_l.get_joint_state(), self._robot_r.get_joint_state())
        )

    def command_joint_state(self, joint_state: np.ndarray) -> None:
        split = self._robot_l.num_dofs()
        pool = self._command_pool
        futures = [
            pool.submit(self._robot_l.command_joint_state, joint_state[:split]),
            pool.submit(self._robot_r.command_joint_state, joint_state[split:]),
        ]
        errors = []
        for future in futures:
            try:
                future.result()
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise RuntimeError(f"bimanual command failed on {len(errors)} arm(s)") from errors[0]

    def get_joint_limits(self) -> np.ndarray:
        return np.vstack(
            (self._robot_l.get_joint_limits(), self._robot_r.get_joint_limits())
        )

    def get_diagnostics(self):
        return {"left": self._robot_l.get_diagnostics(), "right": self._robot_r.get_diagnostics()}

    def get_recording_state(self) -> dict:
        return {"left": self._robot_l.get_recording_state(),
                "right": self._robot_r.get_recording_state()}

    def get_recording_history(self, since):
        return {"left": self._robot_l.get_recording_history(since["left"]),
                "right": self._robot_r.get_recording_history(since["right"])}

    def get_observations(self) -> Dict[str, np.ndarray]:
        l_obs = self._robot_l.get_observations()
        r_obs = self._robot_r.get_observations()
        assert l_obs.keys() == r_obs.keys()
        return_obs = {}
        for k in l_obs.keys():
            try:
                return_obs[k] = np.concatenate((l_obs[k], r_obs[k]))
            except Exception as e:
                print(e)
                print(k)
                print(l_obs[k])
                print(r_obs[k])
                raise RuntimeError()

        return return_obs

    def enter_gravity_comp_idle(self) -> None:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(robot.enter_gravity_comp_idle)
                for robot in (self._robot_l, self._robot_r)
            ]
            for future in futures:
                future.result()

    def enter_safe_hold(self, reason: str | None = None) -> np.ndarray:
        """Capture both followers first, then hold the captured bimanual pose."""
        target = np.asarray(self.get_joint_state(), dtype=float).copy()
        self.command_joint_state(target)
        return target

    def get_health_status(self) -> dict:
        sides = {
            "left": self._robot_l.get_health_status(),
            "right": self._robot_r.get_health_status(),
        }
        failed = [side for side, health in sides.items() if not health.get("ok", False)]
        return {
            "ok": not failed,
            "reason": "" if not failed else f"{', '.join(side.upper() for side in failed)} follower control path unhealthy",
            "sides": sides,
        }

    def close(self, disable_motors: bool = False) -> None:
        self._command_pool.shutdown(wait=True)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(robot.close, disable_motors=disable_motors)
                for robot in (self._robot_l, self._robot_r)
            ]
            errors = []
            for future in futures:
                try:
                    future.result()
                except Exception as exc:
                    errors.append(exc)
            if errors:
                raise RuntimeError(f"failed to close {len(errors)} arm(s)") from errors[0]


class MultiArmRobot(Robot):
    """Execute identical robot operations across a named collection of arms."""

    def __init__(self, robots: dict[str, Robot]):
        if not robots:
            raise ValueError("at least one robot is required")
        self._robots = dict(robots)
        self._command_pool = ThreadPoolExecutor(max_workers=len(robots))

    def num_dofs(self) -> int:
        return sum(robot.num_dofs() for robot in self._robots.values())

    def _slices(self) -> dict[str, slice]:
        start = 0
        slices = {}
        for name, robot in self._robots.items():
            end = start + robot.num_dofs()
            slices[name] = slice(start, end)
            start = end
        return slices

    def get_joint_state(self) -> np.ndarray:
        return np.concatenate([robot.get_joint_state() for robot in self._robots.values()])

    def command_joint_state(self, joint_state: np.ndarray) -> None:
        target = np.asarray(joint_state, dtype=float)
        if target.shape != (self.num_dofs(),):
            raise ValueError(f"expected {self.num_dofs()} joints, got {target.shape}")
        slices = self._slices()
        pool = self._command_pool
        futures = {
            name: pool.submit(robot.command_joint_state, target[slices[name]])
            for name, robot in self._robots.items()
        }
        errors = []
        for name, future in futures.items():
            try:
                future.result()
            except Exception as exc:
                errors.append((name, exc))
        if errors:
            name, error = errors[0]
            raise RuntimeError(
                f"multi-arm command failed on {len(errors)} arm(s), first {name}"
            ) from error

    def get_joint_limits(self) -> np.ndarray:
        return np.vstack([robot.get_joint_limits() for robot in self._robots.values()])

    def get_diagnostics(self):
        return {name: robot.get_diagnostics() for name, robot in self._robots.items()}

    def get_recording_state(self) -> dict:
        return {name: robot.get_recording_state() for name, robot in self._robots.items()}

    def get_recording_history(self, since):
        return {name: robot.get_recording_history(since[name]) for name, robot in self._robots.items()}

    def get_observations(self) -> Dict[str, np.ndarray]:
        observations = [robot.get_observations() for robot in self._robots.values()]
        keys = observations[0].keys()
        if any(obs.keys() != keys for obs in observations[1:]):
            raise RuntimeError("multi-arm observations have mismatched fields")
        return {key: np.concatenate([obs[key] for obs in observations]) for key in keys}

    def enter_gravity_comp_idle(self) -> None:
        with ThreadPoolExecutor(max_workers=len(self._robots)) as pool:
            futures = [pool.submit(robot.enter_gravity_comp_idle) for robot in self._robots.values()]
            for future in futures:
                future.result()

    def enter_safe_hold(self, reason: str | None = None) -> np.ndarray:
        target = np.asarray(self.get_joint_state(), dtype=float).copy()
        self.command_joint_state(target)
        return target

    def get_health_status(self) -> dict:
        sides = {name: robot.get_health_status() for name, robot in self._robots.items()}
        failed = [name for name, health in sides.items() if not health.get("ok", False)]
        return {
            "ok": not failed,
            "reason": "" if not failed else f"{', '.join(failed)} follower control path unhealthy",
            "sides": sides,
        }

    def close(self, disable_motors: bool = False) -> None:
        self._command_pool.shutdown(wait=True)
        with ThreadPoolExecutor(max_workers=len(self._robots)) as pool:
            futures = [
                pool.submit(robot.close, disable_motors=disable_motors)
                for robot in self._robots.values()
            ]
            errors = []
            for future in futures:
                try:
                    future.result()
                except Exception as exc:
                    errors.append(exc)
            if errors:
                raise RuntimeError(f"failed to close {len(errors)} arm(s)") from errors[0]


def main():
    pass


if __name__ == "__main__":
    main()
