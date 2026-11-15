import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict

import numpy as np

from gello.robots.robot import Robot


class YAMRobot(Robot):
    """GELLO wrapper for this station's confirmed YAM Pro followers."""

    # Select motor mapping, dynamics model and PD gains together.
    ARM_TYPE = "yam_pro"

    @classmethod
    def _arm_type(cls):
        from i2rt.robots.utils import ArmType

        return ArmType(cls.ARM_TYPE)

    def __init__(self, channel="can0"):
        from i2rt.robots.get_robot import get_yam_robot

        self.robot = get_yam_robot(channel=channel, arm_type=self._arm_type(), zero_gravity_mode=False)
        self._initialize_wrapper_state()

    def _initialize_wrapper_state(self) -> None:
        """Initialize GELLO-facing state after attaching an i2rt robot."""

        # YAM has 7 joints (6 arm joints + 1 gripper)
        self._joint_names = [
            "joint1",
            "joint2",
            "joint3",
            "joint4",
            "joint5",
            "joint6",
            "gripper",
        ]
        self._joint_state = np.zeros(7)  # 7 joints
        self._joint_velocities = np.zeros(7)  # 7 joints
        self._joint_efforts = np.zeros(7)  # 7 joints
        self._gripper_state = 0.0

    @classmethod
    def _from_hardware_robot(cls, hardware_robot):
        robot = cls.__new__(cls)
        robot.robot = hardware_robot
        robot._initialize_wrapper_state()
        return robot

    @classmethod
    def create_pair(
        cls, left_channel: str, right_channel: str, *, cancel_event=None
    ):
        """Prepare both CAN chains concurrently, then cross a shared start barrier."""
        from i2rt.robots.get_robot import prepare_yam_robot

        channels = {"left": left_channel, "right": right_channel}
        prepared = {}
        errors = []
        print("Preparing LEFT and RIGHT follower motor chains in parallel...", flush=True)

        def progress(side):
            def report(motor_id, total):
                if cancel_event is not None and cancel_event.is_set():
                    raise RuntimeError("follower initialization cancelled")
                print(
                    f"{side.upper()} enabling motor {motor_id}/{total}",
                    flush=True,
                )

            return report

        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="yam_prepare") as pool:
            futures = {
                side: pool.submit(
                    prepare_yam_robot,
                    channel,
                    arm_type=cls._arm_type(),
                    zero_gravity_mode=False,
                    init_progress_callback=progress(side),
                )
                for side, channel in channels.items()
            }
            for side, future in futures.items():
                try:
                    prepared[side] = future.result()
                    print(f"{side.upper()} follower prepared", flush=True)
                except Exception as exc:
                    errors.append((side, exc))

        if errors:
            for item in prepared.values():
                item.rollback()
            side, error = errors[0]
            raise RuntimeError(
                f"{side.upper()} follower prepare failed: {error}"
            ) from error

        if cancel_event is not None and cancel_event.is_set():
            for item in prepared.values():
                item.rollback()
            raise RuntimeError("follower initialization cancelled")

        print("Both followers prepared; starting control threads...", flush=True)
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="yam_start") as pool:
            futures = {
                side: pool.submit(item.start_control)
                for side, item in prepared.items()
            }
            for side, future in futures.items():
                try:
                    future.result()
                except Exception as exc:
                    errors.append((side, exc))

        if errors:
            for item in prepared.values():
                item.rollback()
            side, error = errors[0]
            raise RuntimeError(
                f"{side.upper()} follower control start failed: {error}"
            ) from error

        built = {}
        try:
            # Build sequentially so automatic gripper calibration never moves
            # both grippers at the same time.
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("follower initialization cancelled")
            built["left"] = prepared["left"].build_robot()
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("follower initialization cancelled")
            built["right"] = prepared["right"].build_robot()
        except Exception:
            for item in prepared.values():
                item.rollback()
            raise

        return (
            cls._from_hardware_robot(built["left"]),
            cls._from_hardware_robot(built["right"]),
        )

    @classmethod
    def create_many(cls, channels: dict[str, str], *, cancel_event=None) -> dict[str, "YAMRobot"]:
        """Prepare any number of named YAM follower chains with rollback on failure."""
        from i2rt.robots.get_robot import prepare_yam_robot

        if not channels:
            raise ValueError("at least one YAM channel is required")
        prepared = {}
        errors = []

        def prepare(name: str, channel: str):
            def progress(motor_id, total):
                if cancel_event is not None and cancel_event.is_set():
                    raise RuntimeError("follower initialization cancelled")
                print(f"{name.upper()} enabling motor {motor_id}/{total}", flush=True)

            return prepare_yam_robot(
                channel, arm_type=cls._arm_type(), zero_gravity_mode=False, init_progress_callback=progress
            )

        with ThreadPoolExecutor(max_workers=len(channels), thread_name_prefix="yam_prepare") as pool:
            futures = {name: pool.submit(prepare, name, channel) for name, channel in channels.items()}
            for name, future in futures.items():
                try:
                    prepared[name] = future.result()
                    print(f"{name.upper()} follower prepared", flush=True)
                except Exception as exc:
                    errors.append((name, exc))
        if errors or (cancel_event is not None and cancel_event.is_set()):
            for item in prepared.values():
                item.rollback()
            if errors:
                name, error = errors[0]
                raise RuntimeError(f"{name.upper()} follower prepare failed: {error}") from error
            raise RuntimeError("follower initialization cancelled")

        with ThreadPoolExecutor(max_workers=len(prepared), thread_name_prefix="yam_start") as pool:
            futures = {name: pool.submit(item.start_control) for name, item in prepared.items()}
            for name, future in futures.items():
                try:
                    future.result()
                except Exception as exc:
                    errors.append((name, exc))
        if errors:
            for item in prepared.values():
                item.rollback()
            name, error = errors[0]
            raise RuntimeError(f"{name.upper()} follower control start failed: {error}") from error

        built = {}
        try:
            for name, item in prepared.items():
                if cancel_event is not None and cancel_event.is_set():
                    raise RuntimeError("follower initialization cancelled")
                built[name] = cls._from_hardware_robot(item.build_robot())
        except Exception:
            for item in prepared.values():
                item.rollback()
            raise
        return built

    def num_dofs(self) -> int:
        return 7  # YAM has 7 DOFs

    def get_joint_state(self) -> np.ndarray:
        # Get actual joint positions from I2RT robot (7 joints total)
        joint_pos = self.robot.get_joint_pos()
        # Ensure we have exactly 7 joints
        if len(joint_pos) > 7:
            joint_pos = joint_pos[:7]
        elif len(joint_pos) < 7:
            # Pad with zeros if we have fewer than 7 joints
            joint_pos = np.pad(joint_pos, (0, 7 - len(joint_pos)), "constant")

        self._joint_state = joint_pos
        return self._joint_state

    def command_joint_state(self, joint_state: np.ndarray) -> None:
        assert (
            len(joint_state) == self.num_dofs()
        ), f"Expected {self.num_dofs()} joint values, got {len(joint_state)}"

        dt = 0.01
        self._joint_velocities = (joint_state - self._joint_state) / dt
        self._joint_state = joint_state

        # Command the I2RT robot with all 7 joints (6 arm + 1 gripper)
        self.command_joint_pos(joint_state)

    def get_diagnostics(self):
        return self.robot.get_diagnostics()

    def get_recording_state(self) -> dict:
        return self.robot.get_recording_state()

    def get_recording_history(self, since):
        return self.robot.get_recording_history(since)

    def get_observations(self) -> Dict[str, np.ndarray]:
        hardware_obs = self.robot.get_observations()
        required = {"joint_pos", "joint_vel", "joint_eff"}
        missing = sorted(required - hardware_obs.keys())
        if missing:
            raise RuntimeError(
                f"YAM hardware observation is missing {', '.join(missing)}"
            )
        positions = np.asarray(hardware_obs["joint_pos"], dtype=float)
        velocities = np.asarray(hardware_obs["joint_vel"], dtype=float)
        efforts = np.asarray(hardware_obs["joint_eff"], dtype=float)
        if "gripper_pos" in hardware_obs:
            if "gripper_vel" not in hardware_obs or "gripper_eff" not in hardware_obs:
                raise RuntimeError(
                    "YAM gripper observation is missing velocity or effort"
                )
            positions = np.concatenate(
                [positions, np.asarray(hardware_obs["gripper_pos"], dtype=float)]
            )
            velocities = np.concatenate(
                [velocities, np.asarray(hardware_obs["gripper_vel"], dtype=float)]
            )
            efforts = np.concatenate(
                [efforts, np.asarray(hardware_obs["gripper_eff"], dtype=float)]
            )
        if positions.shape != (7,) or velocities.shape != (7,) or efforts.shape != (7,):
            raise RuntimeError(
                "YAM hardware observation must contain seven position, velocity, "
                "and effort values"
            )
        self._joint_state = positions
        self._joint_velocities = velocities
        self._joint_efforts = efforts
        self._gripper_state = float(self._joint_state[6])
        ee_pos_quat = np.zeros(7)  # Placeholder for FK
        return {
            "joint_positions": self._joint_state,
            "joint_velocities": self._joint_velocities,
            "joint_efforts": self._joint_efforts,
            "ee_pos_quat": ee_pos_quat,
            "gripper_position": np.array([self._gripper_state]),
        }

    def get_joint_pos(self):
        # Get 7 joints from I2RT robot (6 arm + 1 gripper)
        joint_pos = self.robot.get_joint_pos()
        # Ensure we return exactly 7 joints
        if len(joint_pos) > 7:
            joint_pos = joint_pos[:7]
        elif len(joint_pos) < 7:
            # Pad with zeros if we have fewer than 7 joints
            joint_pos = np.pad(joint_pos, (0, 7 - len(joint_pos)), "constant")
        return joint_pos

    def command_joint_pos(self, target_pos):
        # Ensure we send exactly 7 joints to the I2RT robot
        if len(target_pos) > 7:
            target_pos = target_pos[:7]
        elif len(target_pos) < 7:
            # Pad with zeros if we have fewer than 7 joints
            target_pos = np.pad(target_pos, (0, 7 - len(target_pos)), "constant")
        self.robot.command_joint_pos(np.array(target_pos))

    def get_joint_limits(self) -> np.ndarray:
        """Return the hardware model's six arm limits plus normalized gripper."""
        info = self.robot.get_robot_info()
        limits = np.asarray(info.get("joint_limits"), dtype=float)
        if (
            limits.shape != (6, 2)
            or not np.all(np.isfinite(limits))
            or np.any(limits[:, 0] >= limits[:, 1])
        ):
            raise RuntimeError(
                f"YAM hardware returned invalid joint limits: {limits.shape}"
            )
        return np.vstack((limits, np.array([[0.0, 1.0]])))

    def enter_gravity_comp_idle(self) -> None:
        self.robot.enter_gravity_comp_idle()

    def enter_safe_hold(self, reason: str | None = None) -> np.ndarray:
        """Capture actual feedback and hold that exact pose with normal control."""
        target = np.asarray(self.get_joint_state(), dtype=float).copy()
        self.command_joint_state(target)
        return target

    def get_health_status(self, max_feedback_age_s: float = 0.5) -> dict:
        """Return read-only health of both i2rt worker threads and feedback."""
        hardware = self.robot
        chain = getattr(hardware, "motor_chain", None)
        control_thread = getattr(chain, "_control_thread", None)
        server_thread = getattr(hardware, "_server_thread", None)
        joint_state = getattr(hardware, "_joint_state", None)
        timestamp = getattr(joint_state, "timestamp", None)
        feedback_age = None if timestamp is None else max(0.0, time.time() - float(timestamp))
        running = bool(getattr(chain, "running", False))
        control_alive = bool(control_thread and control_thread.is_alive())
        server_alive = bool(server_thread and server_thread.is_alive())
        feedback_fresh = feedback_age is not None and feedback_age <= max_feedback_age_s
        issues = []
        if not running:
            issues.append("motor chain stopped")
        if not control_alive:
            issues.append("CAN control thread stopped")
        if not server_alive:
            issues.append("robot server thread stopped")
        if not feedback_fresh:
            issues.append("feedback stale or unavailable")

        def maximum(name):
            values = getattr(joint_state, name, None)
            return None if values is None or len(values) == 0 else float(np.max(values))

        return {
            "ok": not issues,
            "reason": "; ".join(issues),
            "running": running,
            "control_thread_alive": control_alive,
            "server_thread_alive": server_alive,
            "feedback_age_s": feedback_age,
            "comm_freq_hz": None if chain is None else float(getattr(chain, "comm_freq", 0.0)),
            "max_temp_mos_c": maximum("temp_mos"),
            "max_temp_rotor_c": maximum("temp_rotor"),
        }

    def close(self, disable_motors: bool = False) -> None:
        self.robot.close(disable_motors=disable_motors)


def main():
    robot = YAMRobot()
    print(robot.get_observations())


if __name__ == "__main__":
    main()
