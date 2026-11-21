#!/usr/bin/env python3
"""Persistent owner of both YAM follower CAN chains and safety state."""

from __future__ import annotations

import os
import signal
import threading
import time
from dataclasses import dataclass
from typing import Callable
from pathlib import Path

from gello.safety.identity import daemon_identity

import tyro
from omegaconf import OmegaConf

from gello.robots.robot import BimanualRobot, MultiArmRobot
from gello.robots.yam import YAMRobot
from gello.safety.controller import SafetyController, SafetyState
from gello.safety.daemon_client import SAFETY_DAEMON_PROTOCOL_VERSION
from gello.safety.rpc import DEFAULT_SAFETY_PORT, SafetyRPCServer
from gello.safety.watchdog import verify_motor_watchdogs
from gello.zmq_core.robot_node import ZMQServerRobot


def initialize_followers(
    left_channel: str,
    right_channel: str,
    *,
    additional_channels: dict[str, str] | None = None,
    watchdog_verifier: Callable = verify_motor_watchdogs,
    pair_factory: Callable = YAMRobot.create_pair,
    cancel_event: threading.Event | None = None,
):
    """Verify persistent watchdogs before any motor-enable operation."""
    channels = {"left": left_channel, "right": right_channel, **(additional_channels or {})}
    readings = watchdog_verifier(channels)
    if cancel_event is not None and cancel_event.is_set():
        raise RuntimeError("follower initialization cancelled")
    if additional_channels:
        built = YAMRobot.create_many(channels, cancel_event=cancel_event)
        return MultiArmRobot(built), readings
    if cancel_event is None:
        left, right = pair_factory(left_channel, right_channel)
    else:
        left, right = pair_factory(
            left_channel, right_channel, cancel_event=cancel_event
        )
    return BimanualRobot(left, right), readings


class InitializingController:
    """Minimal RPC-visible state while watchdog checks and motor setup run."""

    def __init__(self, metadata: dict, bootstrap_token: str | None = None):
        self.cancel_event = threading.Event()
        self._state = SafetyState.INITIALIZING
        self._reason = "verifying watchdogs and initializing follower arms"
        self._metadata = metadata
        self._bootstrap_token = bootstrap_token
        self._last_bootstrap_heartbeat = time.monotonic()

    @property
    def state(self):
        return self._state

    def status(self):
        return {
            "state": self._state.value,
            "reason": self._reason,
            "session_id": None,
            "hold_confirmed": False,
            "watchdogs": {},
            **self._metadata,
        }

    def cancel_initialization(self, reason: str):
        self._reason = reason
        self.cancel_event.set()

    def bootstrap_heartbeat(self, token: str):
        if self._bootstrap_token is None or token != self._bootstrap_token:
            raise PermissionError("invalid initialization heartbeat token")
        self._last_bootstrap_heartbeat = time.monotonic()

    def monitor_bootstrap_lease(self):
        while not self.cancel_event.wait(0.1):
            if (
                self._bootstrap_token is not None
                and time.monotonic() - self._last_bootstrap_heartbeat > 1.0
            ):
                self.cancel_initialization("launcher heartbeat expired during initialization")
                return

    def enter_safe_hold(self, reason: str):
        self.cancel_initialization(reason)

    def report_fault(self, reason: str):
        self._state = SafetyState.FAULT
        self._reason = reason

    def disarm(self, session_id: str, confirmation: str):
        if confirmation != "off":
            raise ValueError("motor disable requires exact confirmation: off")
        self.cancel_event.set()
        self._state = SafetyState.DISARMED
        self._reason = "initialization cancelled by explicit off"

    def __getattr__(self, _name):
        raise RuntimeError("followers are still initializing")


@dataclass
class Args:
    left_config_path: str
    right_config_path: str
    third_config_path: str | None = None
    fourth_config_path: str | None = None
    hardware_port: int = 6001
    safety_port: int = DEFAULT_SAFETY_PORT
    bootstrap_token: str | None = None
    gripper_trace_dir: str | None = None
    gripper_trace_hz: float = 250.0


def main() -> int:
    args = tyro.cli(Args)
    if args.gripper_trace_dir is not None:
        import math
        if not math.isfinite(args.gripper_trace_hz) or not 0 < args.gripper_trace_hz <= 1000:
            raise ValueError('gripper trace rate must be > 0 and <= 1000 Hz')
        directory = Path(args.gripper_trace_dir).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        os.environ['YAM_GRIPPER_TRACE_DIR'] = str(directory)
        os.environ['YAM_GRIPPER_TRACE_HZ'] = str(args.gripper_trace_hz)
        print(f'Gripper diagnostics requested: {directory} (up to {args.gripper_trace_hz:g} Hz)', flush=True)
    config_paths = {
        "left": args.left_config_path,
        "right": args.right_config_path,
        **({"third": args.third_config_path} if args.third_config_path else {}),
        **({"fourth": args.fourth_config_path} if args.fourth_config_path else {}),
    }
    channels = {
        name: str(OmegaConf.to_container(OmegaConf.load(path), resolve=True)["robot"]["channel"])
        for name, path in config_paths.items()
    }

    metadata = {
        **daemon_identity(Path(__file__).resolve().parents[1], config_paths),
        "pid": os.getpid(),
        "protocol_version": SAFETY_DAEMON_PROTOCOL_VERSION,
        "hardware_port": args.hardware_port,
        "safety_port": args.safety_port,
        "channels": channels,
    }
    bootstrap = InitializingController(metadata, args.bootstrap_token)
    safety_server = SafetyRPCServer(
        bootstrap, port=args.safety_port, host="127.0.0.1"
    )
    safety_thread = threading.Thread(
        target=safety_server.serve, name="safety_rpc", daemon=True
    )
    safety_thread.start()
    bootstrap_monitor = threading.Thread(
        target=bootstrap.monitor_bootstrap_lease,
        name="bootstrap_heartbeat",
        daemon=True,
    )
    bootstrap_monitor.start()

    def on_initializing_signal(signum, _frame):
        bootstrap.cancel_initialization(f"daemon received signal {signum} during initialization")

    signal.signal(signal.SIGINT, on_initializing_signal)
    signal.signal(signal.SIGTERM, on_initializing_signal)
    try:
        robot, readings = initialize_followers(
            channels["left"],
            channels["right"],
            additional_channels={
                name: channel for name, channel in channels.items() if name not in {"left", "right"}
            },
            cancel_event=bootstrap.cancel_event,
        )
    except Exception as exc:
        bootstrap.report_fault(f"initialization failed: {type(exc).__name__}: {exc}")
        print(bootstrap.status()["reason"], flush=True)
        # Keep status/off available briefly so the launcher receives a precise error.
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and bootstrap.state != SafetyState.DISARMED:
            time.sleep(0.05)
        safety_server.stop()
        safety_thread.join(timeout=1)
        safety_server.close()
        return 1
    controller = SafetyController(
        robot,
        metadata={
            **metadata,
            "watchdogs": readings,
        },
    )
    safety_server.set_controller(controller)

    def on_signal(signum, _frame):
        controller.enter_safe_hold(f"daemon received signal {signum}")

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    robot_server = ZMQServerRobot(
        controller,
        port=args.hardware_port,
        host="127.0.0.1",
        on_error=lambda exc: controller.enter_safe_hold(
            f"robot RPC exception: {type(exc).__name__}: {exc}"
        ),
    )
    def supervised(name, serve):
        try:
            serve()
        except Exception as exc:
            controller.report_fault(
                f"{name} thread crashed: {type(exc).__name__}: {exc}"
            )

    threads = [
        threading.Thread(target=supervised, args=("robot RPC", robot_server.serve), name="robot_rpc", daemon=True),
        safety_thread,
    ]
    threads[0].start()

    print(
        f"Safety daemon ready: watchdogs verified for motors 1-7 on {len(channels)} followers; "
        "state=SAFE_HOLD",
        flush=True,
    )

    try:
        next_health_log = time.monotonic() + 10.0
        while controller.state != SafetyState.DISARMED:
            try:
                controller.check_leases()
                controller.check_hardware_health()
                for thread in threads:
                    if not thread.is_alive():
                        controller.report_fault(f"{thread.name} supervision detected stopped thread")
                if time.monotonic() >= next_health_log:
                    health = controller.status().get("hardware_health", {})
                    summaries = []
                    for side, values in health.get("sides", {}).items():
                        summaries.append(
                            f"{side}: loop={values.get('comm_freq_hz')}Hz "
                            f"mos={values.get('max_temp_mos_c')}C "
                            f"rotor={values.get('max_temp_rotor_c')}C "
                            f"feedback_age={values.get('feedback_age_s')}s"
                        )
                    print("Follower health · " + " · ".join(summaries), flush=True)
                    next_health_log = time.monotonic() + 10.0
            except Exception as exc:
                print(f"Safety monitor fault: {exc}", flush=True)
            time.sleep(0.05)
    finally:
        safety_server.stop()
        robot_server.stop()
        for thread in threads:
            thread.join(timeout=1)
        safety_server.close()
        robot_server.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
