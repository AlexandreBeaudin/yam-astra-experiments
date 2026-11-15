"""Latched safety state machine that exclusively guards follower commands."""

from __future__ import annotations

import enum
import threading
import time
from typing import Callable, Iterable

import numpy as np


class SafetyState(str, enum.Enum):
    INITIALIZING = "initializing"
    SAFE_HOLD = "safe_hold"
    ACTIVE = "active"
    GRAVITY = "gravity"
    FAULT = "fault"
    DISARMED = "disarmed"


class SafetyController:
    """Gate motion behind two live leases and latch all failures in SAFE HOLD."""

    def __init__(
        self,
        robot,
        *,
        required_clients: Iterable[str] = ("parent", "collector"),
        lease_timeout_s: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
        metadata: dict | None = None,
        arm_step_limit_rad: float = 0.20,
        gripper_step_limit: float = 0.25,
        max_consecutive_limited: int = 6,
    ) -> None:
        self._robot = robot
        self._required_clients = frozenset(required_clients)
        self._lease_timeout_s = float(lease_timeout_s)
        self._clock = clock
        self._lock = threading.RLock()
        self._metadata = dict(metadata or {})
        self._arm_step_limit_rad = float(arm_step_limit_rad)
        # Kept as a constructor keyword for source compatibility. Gripper
        # commands are clamped to [0, 1] but intentionally have no slew limit.
        _ = gripper_step_limit
        self._max_consecutive_limited = int(max_consecutive_limited)
        if self._max_consecutive_limited < 1:
            raise ValueError("max_consecutive_limited must be positive")
        self._diagnostic_command = None
        self._session_id: str | None = None
        self._leases: dict[str, float] = {}
        self._state = SafetyState.SAFE_HOLD
        self._reason = "daemon startup"
        self._hold_position: np.ndarray | None = None
        self._last_accepted_target: np.ndarray | None = None
        self._last_command_time = None
        self._timed_control = False
        self._limited_seconds = 0.0
        self._consecutive_limited = 0
        self._hold_confirmed = False
        self._hardware_health: dict = {"ok": True, "sides": {}}
        self._joint_limits = self._read_joint_limits()
        self._capture_safe_hold(self._reason)

    def _read_joint_limits(self) -> np.ndarray:
        getter = getattr(self._robot, "get_joint_limits", None)
        if getter is None:
            raise RuntimeError("follower robot does not publish joint limits")
        limits = np.asarray(getter(), dtype=float)
        expected_shape = (self._robot.num_dofs(), 2)
        if (
            limits.shape != expected_shape
            or not np.all(np.isfinite(limits))
            or np.any(limits[:, 0] >= limits[:, 1])
        ):
            raise RuntimeError(
                f"invalid follower joint limits {limits.shape}, expected {expected_shape}"
            )
        return limits.copy()

    @property
    def state(self) -> SafetyState:
        with self._lock:
            return self._state

    @property
    def reason(self) -> str:
        with self._lock:
            return self._reason

    def _require_session(self, session_id: str) -> None:
        if not self._session_id or session_id != self._session_id:
            raise PermissionError("safety session does not own the daemon")

    def _capture_safe_hold(self, reason: str) -> np.ndarray:
        try:
            if hasattr(self._robot, "enter_safe_hold"):
                target = np.asarray(self._robot.enter_safe_hold(), dtype=float)
            else:
                target = np.asarray(self._robot.get_joint_state(), dtype=float)
                self._robot.command_joint_state(target)
        except Exception as exc:
            self._state = SafetyState.FAULT
            self._reason = f"SAFE HOLD failed after {reason}: {exc}"
            self._hold_confirmed = False
            raise
        self._hold_position = target.copy()
        self._last_accepted_target = target.copy()
        self._consecutive_limited = 0
        self._hold_confirmed = True
        self._state = SafetyState.SAFE_HOLD
        self._reason = reason
        return target

    def begin_session(self, session_id: str) -> None:
        if not session_id:
            raise ValueError("session_id must not be empty")
        with self._lock:
            if self._state == SafetyState.DISARMED:
                raise RuntimeError("followers are disarmed")
            if self._state == SafetyState.FAULT:
                raise RuntimeError(f"followers are faulted: {self._reason}")
            if (
                self._state == SafetyState.ACTIVE
                and session_id != self._session_id
                and not self._stale_clients()
            ):
                raise RuntimeError("another safety session is already active")
            if self._state != SafetyState.SAFE_HOLD:
                self._capture_safe_hold("new session attached")
            self._session_id = session_id
            self._leases.clear()
            self._reason = "waiting for parent and collector heartbeats"

    def heartbeat(self, client_id: str, session_id: str) -> dict:
        with self._lock:
            self._require_session(session_id)
            if client_id not in self._required_clients:
                raise ValueError(f"unknown heartbeat client: {client_id}")
            self._leases[client_id] = self._clock()
            return self.status()

    def _stale_clients(self) -> list[str]:
        now = self._clock()
        return sorted(
            client
            for client in self._required_clients
            if client not in self._leases
            or now - self._leases[client] > self._lease_timeout_s
        )

    def activate(self, session_id: str) -> None:
        with self._lock:
            self._require_session(session_id)
            if self._state != SafetyState.SAFE_HOLD:
                raise RuntimeError("activation is only allowed from SAFE_HOLD")
            stale = self._stale_clients()
            if stale:
                raise RuntimeError(f"missing or stale heartbeat: {', '.join(stale)}")
            self._last_command_time = self._clock()
            self._limited_seconds = 0.0
            self._timed_control = False
            self._state = SafetyState.ACTIVE
            self._reason = "both heartbeat leases are live"

    def check_leases(self) -> bool:
        with self._lock:
            if self._state != SafetyState.ACTIVE:
                return False
            if self._timed_control and self._clock() - self._last_command_time > 0.2:
                self._capture_safe_hold("control command deadline expired")
                return True
            stale = self._stale_clients()
            if not stale:
                return False
            self._capture_safe_hold(f"heartbeat expired: {', '.join(stale)}")
            return True

    def enter_safe_hold(self, reason: str) -> np.ndarray | None:
        with self._lock:
            if self._state == SafetyState.FAULT:
                return None if self._hold_position is None else self._hold_position.copy()
            if self._state in {SafetyState.SAFE_HOLD, SafetyState.DISARMED}:
                if self._state == SafetyState.SAFE_HOLD:
                    self._reason = reason
                return None if self._hold_position is None else self._hold_position.copy()
            return self._capture_safe_hold(reason)

    def enter_gravity(self, session_id: str) -> None:
        with self._lock:
            self._require_session(session_id)
            if self._state != SafetyState.SAFE_HOLD:
                raise RuntimeError("gravity mode is only allowed from SAFE_HOLD")
            self._robot.enter_gravity_comp_idle()
            self._state = SafetyState.GRAVITY
            self._reason = "operator requested gravity compensation"

    def disarm(self, session_id: str, confirmation: str) -> None:
        if confirmation != "off":
            raise ValueError("motor disable requires exact confirmation: off")
        with self._lock:
            if not (
                self._state == SafetyState.FAULT
                and self._session_id is None
                and session_id
            ):
                self._require_session(session_id)
            if self._state == SafetyState.DISARMED:
                return
            if self._state not in {
                SafetyState.SAFE_HOLD,
                SafetyState.GRAVITY,
                SafetyState.FAULT,
            }:
                raise RuntimeError("enter SAFE_HOLD before disabling motors")
            self._robot.close(disable_motors=True)
            self._state = SafetyState.DISARMED
            self._reason = "operator explicitly confirmed off"

    def command_joint_state(self, joint_state: np.ndarray, command_period_s: float | None = None) -> dict:
        with self._lock:
            if self._state != SafetyState.ACTIVE:
                raise RuntimeError(
                    f"motion rejected while safety state is {self._state.value}: "
                    f"{self._reason}"
                )
            target = np.asarray(joint_state, dtype=float)
            expected_shape = (self.num_dofs(),)
            if target.shape != expected_shape or not np.all(np.isfinite(target)):
                problem = (
                    f"invalid command shape {target.shape}, expected {expected_shape}"
                    if target.shape != expected_shape
                    else "command contains NaN or Inf"
                )
                self._capture_safe_hold(problem)
                raise ValueError(problem)

            if self._last_accepted_target is None:
                self._last_accepted_target = np.asarray(
                    self._robot.get_joint_state(), dtype=float
                ).copy()
            now = self._clock()
            step_limit = self._arm_step_limit_rad
            dt = 1 / 30
            if command_period_s is not None:
                if not np.isfinite(command_period_s) or not 0.005 <= command_period_s <= 1/30 + 1e-9:
                    raise ValueError("command period must correspond to 30..200 Hz")
                if self._timed_control and now - self._last_command_time > 0.2:
                    self._capture_safe_hold("control command deadline expired")
                    raise RuntimeError("control command deadline expired")
                dt = min(1 / 30, max(0, now - self._last_command_time))
                step_limit *= 30 * dt
                self._timed_control = True
            elif self._timed_control:
                raise RuntimeError("cannot switch command timing within an active episode")
            desired = target.copy()
            # Every YAM follower has six arm joints followed by one gripper.
            # Keep this derived from the active DOF count so four-pair sessions
            # receive the same clamp as legacy bimanual sessions.
            gripper_indices = list(range(6, desired.size, 7))
            for index in gripper_indices:
                if index < desired.size:
                    desired[index] = np.clip(desired[index], 0.0, 1.0)
            arm_indices = np.array(
                [index for index in range(desired.size) if index not in gripper_indices],
                dtype=int,
            )
            arm_delta = (
                desired[arm_indices] - self._last_accepted_target[arm_indices]
            )
            limited_mask = np.abs(arm_delta) > step_limit
            limited_indices = arm_indices[np.flatnonzero(limited_mask)]
            limited = bool(limited_indices.size)
            applied = desired.copy()
            applied[arm_indices] = self._last_accepted_target[arm_indices] + np.clip(
                arm_delta,
                -step_limit,
                step_limit,
            )
            if limited:
                self._consecutive_limited += 1
                self._limited_seconds += dt
                excessive = (self._limited_seconds >= self._max_consecutive_limited / 30
                             if self._timed_control else self._consecutive_limited >= self._max_consecutive_limited)
                if excessive:
                    count = self._consecutive_limited
                    self._capture_safe_hold(
                        f"{count} consecutive rate-limited command frames"
                    )
                    raise RuntimeError(
                        f"motion rejected after {count} consecutive rate-limited frames"
                    )
            else:
                self._consecutive_limited = 0
                self._limited_seconds = 0.0
            try:
                self._robot.command_joint_state(applied)
            except Exception as exc:
                try:
                    self._capture_safe_hold(f"control exception: {exc}")
                except Exception:
                    pass
                raise
            self._last_accepted_target = applied.copy()
            self._diagnostic_command = {"requested_target": target.copy(),
                                        "accepted_target": applied.copy(),
                                        "accepted_time_ns": time.time_ns()}
            self._last_command_time = now
            result = {
                "applied_joint_state": applied,
                "accepted_time_ns": time.time_ns(),
                "limited": limited,
                "limited_indices": limited_indices.tolist(),
                "consecutive_limited": self._consecutive_limited,
            }
            if self._timed_control:
                result["observation"] = self._robot.get_observations()
            return result

    def check_hardware_health(self) -> bool:
        """Latch FAULT if a follower control path is no longer trustworthy."""
        with self._lock:
            if self._state in {SafetyState.FAULT, SafetyState.DISARMED}:
                return False
            getter = getattr(self._robot, "get_health_status", None)
            if getter is None:
                return False
            try:
                health = dict(getter())
            except Exception as exc:
                health = {"ok": False, "reason": f"health check failed: {exc}", "sides": {}}
            self._hardware_health = health
            if health.get("ok", False):
                return False
            self._state = SafetyState.FAULT
            self._reason = str(health.get("reason") or "follower hardware health check failed")
            # A dead command/feedback thread means software cannot prove that the
            # captured target reached hardware. The motor watchdog is the fallback.
            self._hold_confirmed = False
            return True

    def report_fault(self, reason: str) -> None:
        with self._lock:
            if self._state == SafetyState.DISARMED:
                return
            self._state = SafetyState.FAULT
            self._reason = reason
            self._hold_confirmed = False

    def cancel_initialization(self, reason: str) -> None:
        raise RuntimeError(f"initialization is already complete: {reason}")

    def bootstrap_heartbeat(self, token: str) -> None:
        # Tolerate an initialization heartbeat already in flight while the RPC
        # server atomically switches to the fully initialized controller.
        return None

    def num_dofs(self) -> int:
        return self._robot.num_dofs()

    def get_joint_state(self) -> np.ndarray:
        return self._robot.get_joint_state()

    def get_diagnostics(self):
        with self._lock:
            return {"safety": self.status(), "command": self._diagnostic_command,
                    "followers": self._robot.get_diagnostics()}

    def get_recording_state(self):
        return self._robot.get_recording_state()

    def get_recording_history(self, since):
        return self._robot.get_recording_history(since)

    def get_observations(self):
        return self._robot.get_observations()

    def status(self) -> dict:
        with self._lock:
            return {
                "state": self._state.value,
                "reason": self._reason,
                "session_id": self._session_id,
                "stale_clients": self._stale_clients(),
                "hold_position": None
                if self._hold_position is None
                else self._hold_position.tolist(),
                "hold_confirmed": self._hold_confirmed,
                "consecutive_limited": self._consecutive_limited,
                "hardware_health": self._hardware_health,
                **self._metadata,
                "joint_limits": self._joint_limits.tolist(),
            }
