"""Reusable discovery and startup client for the persistent YAM safety daemon."""

from __future__ import annotations

import subprocess
import time
import uuid
from pathlib import Path

from gello.safety.rpc import SafetyClient

from gello.safety.identity import daemon_identity, validate_daemon_identity

SAFETY_DAEMON_PROTOCOL_VERSION = 6


def probe_safety_daemon(port: int, timeout_ms: int = 150) -> dict | None:
    client = SafetyClient(port=port, timeout_ms=timeout_ms)
    try:
        return client.status()
    except Exception:
        return None
    finally:
        client.close()


def validate_daemon_status(status: dict, channels: dict, hardware_port: int) -> None:
    state = status.get("state")
    if state in {"fault", "disarmed"}:
        raise RuntimeError(f"daemon is {str(state).upper()}: {status.get('reason', '')}")
    if status.get("protocol_version") != SAFETY_DAEMON_PROTOCOL_VERSION:
        raise RuntimeError(
            "daemon software protocol is outdated; safely stop and restart the "
            "safety daemon before continuing"
        )
    if state == "active" and not status.get("stale_clients"):
        raise RuntimeError("another session is ACTIVE; refusing takeover")
    if status.get("channels") != channels:
        raise RuntimeError("daemon CAN channels do not match configuration")
    if int(status.get("hardware_port", -1)) != int(hardware_port):
        raise RuntimeError("daemon robot RPC port does not match")
    expected = {i: 8000 for i in range(1, 8)}
    watchdogs = status.get("watchdogs", {})
    for side in channels:
        actual = {int(key): int(value) for key, value in watchdogs.get(side, {}).items()}
        if actual != expected:
            raise RuntimeError(f"{side.upper()} watchdog verification is missing or invalid")


def ensure_safety_daemon(
    *,
    repo_root: Path,
    python: str,
    left_config: Path,
    right_config: Path,
    third_config: Path | None = None,
    fourth_config: Path | None = None,
    channels: dict,
    hardware_port: int,
    safety_port: int,
    log_path: Path,
    env: dict,
    timeout_s: float = 60.0,
) -> tuple[dict, subprocess.Popen | None]:
    config_paths = {"left": left_config, "right": right_config,
                    **({"third": third_config} if third_config else {}),
                    **({"fourth": fourth_config} if fourth_config else {})}
    expected_identity = daemon_identity(repo_root, config_paths)
    status = probe_safety_daemon(safety_port)
    if status is not None:
        validate_daemon_identity(status, expected_identity)
    process = None
    token = None
    if status is None:
        token = uuid.uuid4().hex
        command = [
            python,
            str(repo_root / "experiments/yam_safety_daemon.py"),
            "--left-config-path", str(left_config),
            "--right-config-path", str(right_config),
            "--hardware-port", str(hardware_port),
            "--safety-port", str(safety_port),
            "--bootstrap-token", token,
        ]
        if third_config is not None:
            command.extend(["--third-config-path", str(third_config)])
        if fourth_config is not None:
            command.extend(["--fourth-config-path", str(fourth_config)])
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                command,
                cwd=repo_root,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )

    deadline = time.monotonic() + timeout_s
    while status is None or status.get("state") == "initializing":
        if process is not None and process.poll() is not None:
            raise RuntimeError(f"safety daemon exited during startup; see {log_path}")
        if time.monotonic() >= deadline:
            client = SafetyClient(port=safety_port)
            try:
                client.cancel_initialization("return-zero startup timeout")
            finally:
                client.close()
            raise RuntimeError("safety daemon startup timed out and was cancelled")
        time.sleep(0.1)
        status = probe_safety_daemon(safety_port)
        if status and status.get("state") == "initializing" and token is not None:
            client = SafetyClient(port=safety_port, timeout_ms=300)
            try:
                client.bootstrap_heartbeat(token)
            finally:
                client.close()
        if status and status.get("state") == "fault":
            raise RuntimeError(f"safety daemon initialization failed: {status.get('reason')}")

    validate_daemon_identity(status, expected_identity)
    validate_daemon_status(status, channels, hardware_port)
    return status, process
