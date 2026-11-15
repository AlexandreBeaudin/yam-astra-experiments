#!/usr/bin/env python3
"""Safely return both YAM followers to their mechanical zero pose."""

from __future__ import annotations

import argparse
import os
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

import numpy as np
import yaml

from gello.command_profile import command_profile, validate_motion_configuration
from gello.safety.daemon_client import ensure_safety_daemon
from gello.safety.motion_session import SupervisedMotionSession
from gello.safety.return_zero import (
    ARM_INDICES,
    GRIPPER_INDICES,
    ZERO_TARGET,
    checked_joints,
    joint_trajectory,
    validate_feedback,
)
from gello.safety.rpc import SafetyClient
from gello.zmq_core.robot_node import ZMQClientRobot

REPO_ROOT = Path(__file__).resolve().parents[1]


def run_return_zero(
    robot,
    safety,
    session_id: str,
    *,
    rate_hz: float = 30.0,
    clock=time.monotonic,
    sleep=time.sleep,
) -> None:
    completed = False
    with SupervisedMotionSession(
        safety,
        session_id,
        operation="return-to-zero",
        clock=clock,
        sleep=sleep,
    ) as session:
        current = checked_joints(robot.get_joint_state(), "current feedback")
        for target in joint_trajectory(current):
            session.check()
            robot.command_joint_state(target)
            sleep(1.0 / rate_hz)
        # Always send the exact endpoint even if interpolation already produced it.
        robot.command_joint_state(ZERO_TARGET.copy())

        valid_samples = 0
        deadline = clock() + 3.0
        last_error = np.full(14, np.inf)
        while clock() < deadline:
            session.check()
            valid, last_error = validate_feedback(robot.get_joint_state())
            valid_samples = valid_samples + 1 if valid else 0
            if valid_samples >= 3:
                completed = True
                break
            sleep(1.0 / rate_hz)
        if not completed:
            raise RuntimeError(
                "zero verification timed out; max arm error "
                f"{last_error[ARM_INDICES].max():.3f} rad, max gripper error "
                f"{last_error[GRIPPER_INDICES].max():.3f}"
            )


def load_config(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    if not isinstance(config, dict):
        raise ValueError(f"invalid configuration: {path}")
    expected = ZERO_TARGET[:7]
    actual = np.asarray(config["agent"]["start_joints"], dtype=float)
    if actual.shape != (7,) or not np.array_equal(actual, expected):
        raise ValueError(f"{path}: start_joints must equal {expected.tolist()}")
    return config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="yam return-zero",
        description="Return both YAM followers to mechanical zero",
    )
    parser.add_argument("--profile", type=Path, help="machine profile (defaults to YAM_PROFILE/XDG)")
    parser.add_argument("--left-config", type=Path)
    parser.add_argument("--right-config", type=Path)
    parser.add_argument("--hardware-port", type=int)
    parser.add_argument("--safety-port", type=int)
    parser.add_argument("--startup-timeout", type=float, default=60.0)
    parser.add_argument("-y", "--yes", action="store_true", help="skip confirmation prompt")
    args = parser.parse_args(argv)
    profile = command_profile(parser, args.profile)
    assert profile is not None
    args.left_config = args.left_config or (
        profile.arms["left"].config_path
    )
    args.right_config = args.right_config or (
        profile.arms["right"].config_path
    )
    args.hardware_port = args.hardware_port if args.hardware_port is not None else (
        profile.network.hardware_port
    )
    args.safety_port = args.safety_port if args.safety_port is not None else (
        profile.network.safety_port
    )

    effective_configs = {"left": args.left_config, "right": args.right_config}
    validate_motion_configuration(
        parser, effective_configs,
        hardware_port=args.hardware_port, safety_port=args.safety_port,
    )
    safety = None
    robot = None
    try:
        left = load_config(args.left_config.resolve())
        right = load_config(args.right_config.resolve())
        channels = {
            "left": str(left["robot"]["channel"]),
            "right": str(right["robot"]["channel"]),
        }
        workspace = REPO_ROOT
        env = os.environ.copy()
        env["PYTHONPATH"] = (
            f"{workspace}{os.pathsep}{env['PYTHONPATH']}"
            if env.get("PYTHONPATH")
            else str(workspace)
        )
        env["PYTHONUNBUFFERED"] = "1"
        state_dir = Path(env.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "yam-teleop"
        log_path = state_dir / datetime.now().strftime("safety-%Y%m%d-%H%M%S.log")
        status, _process = ensure_safety_daemon(
            repo_root=REPO_ROOT,
            python=sys.executable,
            left_config=args.left_config.resolve(),
            right_config=args.right_config.resolve(),
            channels=channels,
            hardware_port=args.hardware_port,
            safety_port=args.safety_port,
            log_path=log_path,
            env=env,
            timeout_s=args.startup_timeout,
        )
        safety = SafetyClient(port=args.safety_port)
        robot = ZMQClientRobot(port=args.hardware_port)
        current = checked_joints(robot.get_joint_state(), "current feedback")
        print(f"Safety state: {status['state'].upper()}")
        print(f"Current: {np.array2string(current, precision=3)}")
        print(f"Target : {ZERO_TARGET.astype(int)}")
        max_displacement = np.abs(current - ZERO_TARGET)[ARM_INDICES].max()
        print(f"Maximum arm displacement: {max_displacement:.3f} rad")
        if not args.yes:
            input(
                "Clear the workspace and keep emergency stop ready. "
                "Press Enter to return to zero · Ctrl+C to cancel "
            )
        run_return_zero(robot, safety, uuid.uuid4().hex)
        print("✓ Both followers reached zero · SAFE HOLD ACTIVE")
        return 0
    except KeyboardInterrupt:
        if safety is not None:
            safety.safe_hold("return-to-zero cancelled by operator")
        print("\nCancelled · followers remain in SAFE HOLD")
        return 130
    except Exception as exc:
        print(f"Return-to-zero failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if robot is not None:
            robot.close()
        if safety is not None:
            safety.close()


if __name__ == "__main__":
    raise SystemExit(main())
