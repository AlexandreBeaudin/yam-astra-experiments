"""Read-only diagnostics for a configured YAM workstation."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

from gello.profile import MachineProfile


@dataclass(frozen=True)
class CheckResult:
    check: str
    status: str
    summary: str
    details: dict[str, Any] = field(default_factory=dict)


def _result(
    check: str, status: str, summary: str, **details: Any
) -> CheckResult:
    return CheckResult(check, status, summary, details)


def _software_checks() -> list[CheckResult]:
    results: list[CheckResult] = []
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if sys.version_info[:2] == (3, 11):
        results.append(_result("software.python", "PASS", f"Python {version}"))
    else:
        results.append(
            _result(
                "software.python",
                "FAIL",
                f"Python {version}; required >=3.11,<3.12",
            )
        )
    required = {
        "yaml": "PyYAML",
        "numpy": "numpy",
        "h5py": "h5py",
        "zmq": "pyzmq",
        "rich": "rich",
        "i2rt": "i2rt (install the yam extra)",
    }
    for module, label in required.items():
        found = importlib.util.find_spec(module) is not None
        results.append(
            _result(
                f"software.{module}",
                "PASS" if found else "FAIL",
                f"{label} {'available' if found else 'missing'}",
            )
        )
    return results


def _profile_checks(profile: MachineProfile) -> list[CheckResult]:
    return [
        _result(
            "profile.schema",
            "PASS",
            f"{profile.name} schema v{profile.schema_version}",
            path=str(profile.path),
        ),
        _result(
            "profile.arms",
            "PASS",
            "left/right arm references are valid and distinct",
            left=str(profile.arms["left"].config_path),
            right=str(profile.arms["right"].config_path),
        ),
        _result(
            "profile.auxiliary_pairs",
            "PASS",
            (
                f"{len(profile.auxiliary_pairs)} auxiliary pair(s) registered"
                if profile.auxiliary_pairs
                else "no auxiliary pairs registered"
            ),
            pairs={
                name: {
                    "leader": str(pair.leader_port),
                    "can": pair.can_channel,
                    "can_adapter_serial": pair.can_adapter_serial,
                }
                for name, pair in profile.auxiliary_pairs.items()
            },
        ),
        _result(
            "profile.safety",
            "PASS",
            (
                f"{profile.safety.profile}: {profile.safety.watchdog_ms} ms, "
                f"{profile.safety.motors_per_arm} motors/arm"
            ),
        ),
    ]


def storage_checks(profile: MachineProfile) -> list[CheckResult]:
    storage = profile.storage
    results: list[CheckResult] = []
    if not storage.root.is_dir():
        results.append(
            _result(
                "storage.root",
                "FAIL",
                f"data root is not a directory: {storage.root}",
            )
        )
    else:
        usage = shutil.disk_usage(storage.root)
        free_gb = usage.free / 1_000_000_000
        status = "PASS" if free_gb >= storage.min_free_gb else "FAIL"
        results.append(
            _result(
                "storage.root",
                status,
                f"{free_gb:.1f} GB free (minimum {storage.min_free_gb:.1f} GB)",
                path=str(storage.root),
                free_bytes=usage.free,
            )
        )
        writable = os.access(storage.root, os.W_OK | os.X_OK)
        results.append(
            _result(
                "storage.permissions",
                "PASS" if writable else "FAIL",
                f"data root is {'writable' if writable else 'not writable'}",
            )
        )
    link = storage.workspace_link
    if link is not None:
        if not link.is_symlink():
            results.append(
                _result(
                    "storage.link",
                    "FAIL",
                    f"workspace data path is not a symlink: {link}",
                )
            )
        else:
            try:
                target = link.resolve(strict=True)
                expected = storage.root.resolve(strict=True)
            except OSError as exc:
                results.append(
                    _result("storage.link", "FAIL", f"broken data symlink: {exc}")
                )
            else:
                results.append(
                    _result(
                        "storage.link",
                        "PASS" if target == expected else "FAIL",
                        f"{link} → {target}",
                        expected=str(expected),
                    )
                )
    return results


def _serial_checks(profile: MachineProfile) -> list[CheckResult]:
    results = []
    for side, arm in profile.arms.items():
        exists = arm.leader_port.exists()
        accessible = exists and os.access(arm.leader_port, os.R_OK | os.W_OK)
        status = "PASS" if accessible else "FAIL"
        summary = (
            f"{arm.leader_port} ready"
            if accessible
            else f"{arm.leader_port} {'permission denied' if exists else 'missing'}"
        )
        details: dict[str, Any] = {}
        if exists:
            try:
                details["device"] = str(arm.leader_port.resolve(strict=True))
            except OSError:
                pass
        results.append(_result(f"leader.{side}", status, summary, **details))
    for name, pair in profile.auxiliary_pairs.items():
        exists = pair.leader_port.exists()
        accessible = exists and os.access(pair.leader_port, os.R_OK | os.W_OK)
        status = "PASS" if accessible else "FAIL"
        if accessible:
            role = "four-pair configured" if pair.config_path is not None else "inventory only"
            summary = f"{pair.leader_port} ready (auxiliary pair; {role})"
        else:
            summary = f"{pair.leader_port} {'permission denied' if exists else 'missing'}"
        results.append(
            _result(
                f"leader.{name}",
                status,
                summary,
                can_adapter_serial=pair.can_adapter_serial,
            )
        )
    return results


def _can_check(side: str, channel: str) -> CheckResult:
    try:
        completed = subprocess.run(
            ["ip", "-details", "link", "show", channel],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except FileNotFoundError:
        return _result(f"can.{side}", "FAIL", "ip command is missing")
    except subprocess.TimeoutExpired:
        return _result(f"can.{side}", "FAIL", f"inspection timed out: {channel}")
    output = f"{completed.stdout}\n{completed.stderr}"
    if completed.returncode:
        return _result(f"can.{side}", "FAIL", f"CAN interface missing: {channel}")
    state_match = re.search(r"\bcan state\s+(\S+)", output)
    bitrate_match = re.search(r"\bbitrate\s+(\d+)", output)
    state = state_match.group(1) if state_match else "UNKNOWN"
    bitrate = int(bitrate_match.group(1)) if bitrate_match else None
    is_up = bool(re.search(r"\bstate UP\b", output))
    healthy = is_up and state == "ERROR-ACTIVE" and bitrate == 1_000_000
    return _result(
        f"can.{side}",
        "PASS" if healthy else "FAIL",
        f"{channel}: {state}, bitrate {bitrate or 'unknown'}",
        link_up=is_up,
        can_state=state,
        bitrate=bitrate,
    )


def _network_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.15):
            return True
    except OSError:
        return False


def _daemon_checks(profile: MachineProfile) -> list[CheckResult]:
    from gello.safety.daemon_client import probe_safety_daemon

    status = probe_safety_daemon(profile.network.safety_port, timeout_ms=200)
    hardware_open = _network_open(
        profile.network.host, profile.network.hardware_port
    )
    if status is None:
        return [
            _result(
                "network.hardware",
                "WARN" if hardware_open else "PASS",
                (
                    f"{profile.network.hardware_port} is occupied without an attachable daemon"
                    if hardware_open
                    else f"{profile.network.hardware_port} is available"
                ),
            ),
            _result(
                "safety.daemon",
                "SKIP",
                "safety daemon is not running; watchdog state cannot be observed",
            ),
        ]
    results = [
        _result(
            "network.hardware",
            "PASS" if hardware_open else "FAIL",
            (
                f"robot RPC listening on {profile.network.hardware_port}"
                if hardware_open
                else f"daemon is present but robot RPC {profile.network.hardware_port} is closed"
            ),
        )
    ]
    from pathlib import Path
    from gello.safety.identity import daemon_identity, validate_daemon_identity
    try:
        validate_daemon_identity(status, daemon_identity(
            Path(__file__).resolve().parents[1],
            {side: arm.config_path for side, arm in profile.arms.items()}))
    except Exception as exc:
        results.append(_result("safety.identity", "FAIL", str(exc)))
    else:
        results.append(_result("safety.identity", "PASS", "daemon code, owner and motor configuration match"))
    daemon_state = str(status.get("state", "unknown"))
    state_ok = daemon_state in {"safe_hold", "active", "gravity"}
    results.append(
        _result(
            "safety.daemon",
            "PASS" if state_ok else "FAIL",
            f"daemon state {daemon_state.upper()}",
            pid=status.get("pid"),
            reason=status.get("reason"),
        )
    )
    expected_raw = int(profile.safety.watchdog_ms / 0.05)
    watchdogs = status.get("watchdogs", {})
    for side in ("left", "right"):
        actual = {
            int(key): int(value)
            for key, value in dict(watchdogs.get(side, {})).items()
        }
        expected = {
            motor_id: expected_raw
            for motor_id in range(1, profile.safety.motors_per_arm + 1)
        }
        results.append(
            _result(
                f"safety.watchdogs.{side}",
                "PASS" if actual == expected else "FAIL",
                (
                    f"{len(actual)} watchdogs verified at {profile.safety.watchdog_ms} ms"
                    if actual == expected
                    else "watchdog report is missing or does not match yam-v1"
                ),
                actual=actual,
            )
        )
    return results


def _camera_checks(profile: MachineProfile, *, deep: bool) -> list[CheckResult]:
    if profile.cameras is None:
        return [_result("cameras", "SKIP", "no camera rig configured")]
    try:
        from gello.cameras.realsense_rig import (
            CameraRig,
            enumerate_realsense_devices,
            load_camera_rig_config,
        )
    except ImportError as exc:
        status = "FAIL" if profile.cameras.required else "WARN"
        return [_result("cameras.sdk", status, f"RealSense SDK unavailable: {exc}")]
    try:
        config = load_camera_rig_config(profile.cameras.config_path)
        inventory = enumerate_realsense_devices()
    except Exception as exc:
        status = "FAIL" if profile.cameras.required else "WARN"
        return [_result("cameras.inventory", status, f"{type(exc).__name__}: {exc}")]
    by_serial = {device.serial: device for device in inventory}
    results = []
    for name, spec in config.cameras.items():
        found = by_serial.get(spec.serial)
        valid = found is not None and found.model == spec.model
        results.append(
            _result(
                f"camera.{name}",
                "PASS" if valid else ("FAIL" if profile.cameras.required else "WARN"),
                (
                    f"{spec.model} {spec.serial} connected"
                    if valid
                    else f"{spec.model} {spec.serial} missing or model mismatch"
                ),
                usb=getattr(found, "usb_type_descriptor", None),
                firmware=getattr(found, "firmware_version", None),
            )
        )
    if deep and all(item.status == "PASS" for item in results):
        rig = CameraRig(config, depth_enabled=False)
        try:
            rig.start()
            health = rig.status()
            stable = all(item.state == "streaming" for item in health.values())
            results.append(
                _result(
                    "cameras.stream",
                    "PASS" if stable else "FAIL",
                    "RGB streams are stable" if stable else "one or more RGB streams are unhealthy",
                    health={key: asdict(value) for key, value in health.items()},
                )
            )
        except Exception as exc:
            results.append(
                _result(
                    "cameras.stream",
                    "FAIL",
                    f"{type(exc).__name__}: {exc}",
                )
            )
        finally:
            rig.close()
    return results


def run_doctor(
    profile: MachineProfile,
    *,
    deep: bool = False,
    offline: bool = False,
) -> list[CheckResult]:
    """Run diagnostics only; this function never starts or changes robot control."""
    checks = _software_checks() + _profile_checks(profile) + storage_checks(profile)
    if offline:
        checks.extend(
            [
                _result("leaders", "SKIP", "offline mode"),
                _result("can", "SKIP", "offline mode"),
                _result("cameras", "SKIP", "offline mode"),
                _result("safety.daemon", "SKIP", "offline mode"),
            ]
        )
        return checks
    checks.extend(_serial_checks(profile))
    checks.extend(
        _can_check(side, arm.can_channel) for side, arm in profile.arms.items()
    )
    checks.extend(
        _can_check(name, pair.can_channel)
        for name, pair in profile.auxiliary_pairs.items()
    )
    checks.extend(_camera_checks(profile, deep=deep))
    checks.extend(_daemon_checks(profile))
    return checks


def exit_code(results: Iterable[CheckResult]) -> int:
    return 1 if any(result.status == "FAIL" for result in results) else 0


def json_report(profile: MachineProfile, results: list[CheckResult]) -> str:
    payload = {
        "schema_version": 1,
        "profile": {"name": profile.name, "path": str(profile.path)},
        "summary": {
            status: sum(result.status == status for result in results)
            for status in ("PASS", "WARN", "FAIL", "SKIP")
        },
        "checks": [asdict(result) for result in results],
    }
    return json.dumps(payload, indent=2, sort_keys=True)
