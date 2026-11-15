#!/usr/bin/env python3
"""Concise, safety-oriented launcher for YAM multi-arm teleoperation."""

from __future__ import annotations

import argparse
import json
import math
import os
import queue
import re
import select
import signal
import subprocess
import sys
import termios
import threading
import time
import tty
import uuid
import webbrowser
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from dynamixel_sdk import COMM_SUCCESS, PacketHandler, PortHandler
from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.text import Text

from gello.cameras.realsense_rig import (
    CameraDeviceInfo,
    CameraRig,
    CameraRigConfig,
    enumerate_realsense_devices,
    load_camera_rig_config,
)
from gello.command_profile import command_profile, validate_motion_configuration
from gello.data_utils.collection_protocol import parse_event
from gello.data_utils.episode_duration import (
    format_duration,
    inspect_episode_paths,
)
from gello.data_utils.episode_writer import (
    EpisodeWriter,
    ensure_data_link,
    validate_task_name,
)
from gello.doctor import storage_checks
from gello.safety.daemon_client import ensure_safety_daemon
from gello.safety.rpc import SafetyClient
from gello.teleop_language import (
    TerminalArgumentParser,
    diagnostic,
    label,
    language,
    terminal_language,
    tr,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LEFT_CONFIG = REPO_ROOT / "configs/yam_left.yaml"
DEFAULT_RIGHT_CONFIG = REPO_ROOT / "configs/yam_right.yaml"
DEFAULT_CAMERA_CONFIG = REPO_ROOT / "configs/realsense_bimanual.yaml"


@dataclass
class ArmStatus:
    name: str
    leader_port: str
    leader_id: str
    can_channel: str
    serial_exists: bool
    serial_accessible: bool
    can_ok: bool
    can_state: str
    bitrate: int | None
    latency_ms: int | None
    leader_ids: tuple[int, ...]
    leader_error: str | None

    @property
    def ready(self) -> bool:
        return (
            self.serial_exists
            and self.serial_accessible
            and self.latency_ms == 1
            and self.leader_error is None
            and self.can_ok
            and self.bitrate == 1_000_000
        )


@dataclass(frozen=True)
class CameraStatus:
    name: str
    model: str
    serial: str
    profile: str
    usb: str
    state: str

    @property
    def ready(self) -> bool:
        return self.state in {"READY", "USB WARNING"}


def _usb3_or_newer(descriptor: str) -> bool:
    match = re.match(r"\s*(\d+)(?:\.(\d+))?", descriptor)
    return match is not None and int(match.group(1)) >= 3


def inspect_cameras(
    config: CameraRigConfig,
    inventory: tuple[CameraDeviceInfo, ...],
) -> tuple[list[CameraStatus], list[str]]:
    """Match the fixed logical cameras to read-only SDK inventory."""
    errors: list[str] = []
    by_serial: dict[str, CameraDeviceInfo] = {}
    duplicates: set[str] = set()
    for device in inventory:
        if device.serial in by_serial:
            duplicates.add(device.serial)
        by_serial[device.serial] = device
    for serial in sorted(duplicates):
        errors.append(f"duplicate RealSense serial reported by SDK: {serial}")

    statuses: list[CameraStatus] = []
    for name, spec in config.cameras.items():
        device = by_serial.get(spec.serial)
        state = "READY"
        usb = "—"
        if device is None:
            state = "MISSING"
            errors.append(f"{name} camera is not connected: {spec.model} {spec.serial}")
        else:
            usb = device.usb_type_descriptor or "unknown"
            if device.model != spec.model:
                state = "MODEL"
                errors.append(f"{name} model mismatch: expected {spec.model}, got {device.model}")
            elif re.fullmatch(r"\s*2(?:\.\d+)*\s*", usb):
                state = "USB WARNING"
            elif not _usb3_or_newer(usb):
                state = "USB CHECK"
                errors.append(f"{name} USB link must be 3.x, got {usb}")
        statuses.append(
            CameraStatus(
                name=name,
                model=spec.model,
                serial=spec.serial,
                profile=f"{spec.width}×{spec.height}@{spec.fps}",
                usb=usb,
                state=state,
            )
        )
    return statuses, errors


def run_camera_stream_check(
    config: CameraRigConfig,
    *,
    depth_enabled: bool,
    rig_factory=CameraRig,
):
    """Temporarily stream and warm all cameras without touching robot control."""
    rig = rig_factory(config, depth_enabled=depth_enabled)
    try:
        rig.start()
        return rig.status()
    finally:
        rig.close()


def serial_state_label(exists: bool, accessible: bool) -> str:
    if not exists:
        return "MISSING"
    if not accessible:
        return "PERMISSION"
    return "READY"


def load_config(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = yaml.safe_load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"configuration is not a mapping: {path}")
    return value


def short_serial(port: str) -> str:
    match = re.search(r"Converter_([^-]+)-if", port)
    return match.group(1) if match else Path(port).name


def serial_latency(port: str) -> int | None:
    try:
        tty = Path(port).resolve(strict=True).name
        latency_path = Path("/sys/bus/usb-serial/devices") / tty / "latency_timer"
        return int(latency_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def serial_latency_path(port: str) -> Path | None:
    try:
        tty_name = Path(port).resolve(strict=True).name
    except OSError:
        return None
    return Path("/sys/bus/usb-serial/devices") / tty_name / "latency_timer"


def inspect_leader_bus(
    port: str,
    expected_ids: tuple[int, ...],
    *,
    baudrate: int = 57_600,
) -> tuple[tuple[int, ...], str | None]:
    """Read-only broadcast ping for every configured GELLO Dynamixel."""
    port_handler = PortHandler(port)
    opened = False
    try:
        opened = bool(port_handler.openPort())
        if not opened:
            return (), "could not open the serial port"
        if not port_handler.setBaudRate(baudrate):
            return (), f"could not set baudrate {baudrate}"
        found, result = PacketHandler(2.0).broadcastPing(port_handler)
        found_ids = tuple(sorted(int(dxl_id) for dxl_id in found))
        missing = tuple(dxl_id for dxl_id in expected_ids if dxl_id not in found)
        if missing:
            detail = PacketHandler(2.0).getTxRxResult(result)
            return (
                found_ids,
                f"missing Dynamixel IDs {list(missing)}; detected {list(found_ids)} "
                f"({detail}, code {result})",
            )
        if result != COMM_SUCCESS:
            detail = PacketHandler(2.0).getTxRxResult(result)
            return found_ids, f"broadcast ping failed: {detail} (code {result})"
        return found_ids, None
    except Exception as exc:
        return (), f"broadcast ping error: {type(exc).__name__}: {exc}"
    finally:
        if opened:
            port_handler.closePort()


def can_details(channel: str) -> tuple[bool, str, int | None]:
    try:
        result = subprocess.run(
            ["ip", "-details", "link", "show", channel],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return False, "ip command missing", None

    output = f"{result.stdout}\n{result.stderr}"
    if result.returncode != 0:
        return False, "missing", None

    state_match = re.search(r"\bcan state\s+(\S+)", output)
    bitrate_match = re.search(r"\bbitrate\s+(\d+)", output)
    link_up = bool(re.search(r"\bstate UP\b", output))
    state = state_match.group(1) if state_match else ("UP" if link_up else "DOWN")
    bitrate = int(bitrate_match.group(1)) if bitrate_match else None
    return link_up and state == "ERROR-ACTIVE", state, bitrate


def inspect_arm(name: str, config: dict[str, Any]) -> ArmStatus:
    leader_port = str(config["agent"]["port"])
    can_channel = str(config["robot"]["channel"])
    can_ok, can_state, bitrate = can_details(can_channel)
    serial_exists = Path(leader_port).exists()
    serial_accessible = serial_exists and os.access(leader_port, os.R_OK | os.W_OK)
    motor = config["agent"].get("dynamixel_config", {})
    expected_ids = tuple(int(item) for item in motor.get("joint_ids", ()))
    gripper = motor.get("gripper_config", ())
    if gripper:
        expected_ids += (int(gripper[0]),)
    leader_ids: tuple[int, ...] = ()
    leader_error = None
    if serial_accessible:
        leader_ids, leader_error = inspect_leader_bus(leader_port, expected_ids)
    return ArmStatus(
        name=name,
        leader_port=leader_port,
        leader_id=short_serial(leader_port),
        can_channel=can_channel,
        serial_exists=serial_exists,
        serial_accessible=serial_accessible,
        can_ok=can_ok,
        can_state=can_state,
        bitrate=bitrate,
        latency_ms=serial_latency(leader_port),
        leader_ids=leader_ids,
        leader_error=leader_error,
    )


def validate_configs(configs: dict[str, dict[str, Any]], server_port: int) -> list[str]:
    errors: list[str] = []
    ports = [str(config["agent"]["port"]) for config in configs.values()]
    channels = [str(config["robot"]["channel"]) for config in configs.values()]
    rates = [int(config.get("hz", 30)) for config in configs.values()]
    if len(set(ports)) != len(ports):
        errors.append("leader serial ports must be unique")
    if len(set(channels)) != len(channels):
        errors.append("follower CAN channels must be unique")
    if len(set(rates)) != 1:
        errors.append("all control rates must match")
    for name, config in configs.items():
        motor = config["agent"].get("dynamixel_config", {})
        for key in ("joint_ids", "joint_offsets", "joint_signs"):
            if len(motor.get(key, [])) != 6:
                errors.append(f"{name} {key} must contain 6 values")
        if len(motor.get("gripper_config", [])) != 3:
            errors.append(f"{name} gripper_config must contain 3 values")
        if len(config["agent"].get("start_joints", [])) != 7:
            errors.append(f"{name} start_joints must contain 7 values")
    return errors


def render_preflight(
    console: Console, arms: list[ArmStatus], rate_hz: int, server_port: int
) -> None:
    table = Table(
        title=tr("YAM {0}-Arm Teleoperation").format(len(arms)), box=None, show_header=True
    )
    table.add_column(tr("SIDE"), style="bold")
    table.add_column(tr("GELLO"))
    table.add_column(tr("FOLLOWER"))
    table.add_column("CAN")
    table.add_column("FTDI")
    table.add_column(tr("STATE"), justify="right")
    for arm in arms:
        bitrate = f"{arm.bitrate / 1_000_000:g} Mbps" if arm.bitrate else "—"
        latency = f"{arm.latency_ms} ms" if arm.latency_ms is not None else "—"
        if arm.ready:
            state = tr("[green]READY[/green]")
        elif not arm.serial_exists or not arm.serial_accessible:
            state = f"[red]{tr(serial_state_label(arm.serial_exists, arm.serial_accessible))}[/red]"
        elif arm.latency_ms != 1:
            state = tr("[red]FTDI CHECK[/red]")
        elif arm.leader_error is not None:
            state = tr("[red]GELLO CHECK[/red]")
        else:
            state = tr("[red]CAN CHECK[/red]")
        table.add_row(label(arm.name), arm.leader_id, arm.can_channel, bitrate, latency, state)
    table.caption = tr("target {0} Hz  ·  control 127.0.0.1:{1}").format(rate_hz, server_port)
    console.print(table)


def render_camera_preflight(
    console: Console, cameras: list[CameraStatus], *, depth_enabled: bool
) -> None:
    table = Table(title=tr("RealSense Capture"), box=None, show_header=True)
    table.add_column(tr("VIEW"), style="bold")
    table.add_column(tr("MODEL"))
    table.add_column(tr("SERIAL"))
    table.add_column(tr("PROFILE"))
    table.add_column("USB")
    table.add_column(tr("STATE"), justify="right")
    for camera in cameras:
        state = tr("[green]READY[/green]") if camera.ready else f"[red]{label(camera.state)}[/red]"
        if camera.state == "USB WARNING":
            state = f"[yellow]{tr('USB WARNING')}[/yellow]"
        table.add_row(
            label(camera.name),
            camera.model,
            camera.serial,
            camera.profile,
            camera.usb,
            state,
        )
    table.caption = tr("RGB ON  ·  depth {0}").format(tr("ON") if depth_enabled else tr("OFF"))
    console.print(table)

    for camera in cameras:
        if camera.state == "USB WARNING":
            console.print(Text(tr(
                "Warning: {0} camera uses USB {1}; bandwidth is limited. Continuing stream checks and startup; stable FPS is not guaranteed."
            ).format(label(camera.name), camera.usb), style="yellow"))


def output_reader(stream: Any, log_path: Path, messages: queue.Queue[str]) -> None:
    with log_path.open("w", encoding="utf-8", errors="replace") as log_file:
        for line in iter(stream.readline, ""):
            log_file.write(line)
            log_file.flush()
            messages.put(line.rstrip("\r\n"))
    stream.close()


def drain(messages: queue.Queue[str], history: list[str]) -> list[str]:
    current: list[str] = []
    while True:
        try:
            line = messages.get_nowait()
        except queue.Empty:
            break
        if line:
            history.append(line)
            del history[:-30]
            current.append(line)
    return current


def startup_label(lines: list[str], current: str) -> tuple[str, bool]:
    ready = False
    for line in lines:
        event = parse_event(line)
        if event is not None and event.get("type") == "episode_started":
            current = tr("Episode {0:04d} recording").format(int(event["episode_index"]))
            ready = True
            continue
        if event is not None and event.get("type") == "sync_started":
            current = tr("Synchronizing followers to GELLO leaders · not recording")
            continue
        if event is not None and event.get("type") == "sync_progress":
            side = label(event.get("worst_side", ""))
            joint = event.get("worst_joint")
            worst = tr("{0} J{1} · ").format(side, int(joint)) if side and joint is not None else ""
            current = tr("Synchronizing followers · {0}arm error {1:.3f} rad").format(
                worst, float(event.get("max_arm_error", 0.0))
            )
            continue
        if event is not None and event.get("type") in {"ready", "next_ready"}:
            current = tr("Followers held · ready for leader synchronization")
            ready = True
            continue
        if "attempting to connect to port" in line.lower():
            current = tr("Connecting GELLO leaders")
        elif "Successfully initialized Dynamixel driver" in line:
            current = tr("GELLO leader connected")
        elif "Preparing LEFT and RIGHT follower motor chains" in line:
            current = tr("Preparing follower arms")
        elif "LEFT enabling motor" in line:
            current = diagnostic(line.strip())
        elif "RIGHT enabling motor" in line:
            current = diagnostic(line.strip())
        elif "LEFT follower prepared" in line:
            current = tr("LEFT follower prepared · waiting for RIGHT")
        elif "RIGHT follower prepared" in line:
            current = tr("RIGHT follower prepared · waiting for LEFT")
        elif "Both followers prepared; starting control threads" in line:
            current = tr("Both followers prepared · starting control")
        elif "Waiting for hardware server" in line:
            current = tr("Starting follower control server")
        elif "Hardware server ready" in line:
            current = tr("Followers connected")
        elif "Captured follower session home" in line:
            current = tr("Follower home pose captured")
        elif "Moving robot to start position" in line:
            current = tr("Moving followers to start pose")
        elif "Launching robot:" in line:
            current = tr("Starting control loop")
        elif "Start 🚀" in line:
            current = tr("Teleoperation live")
            ready = True
    return current, ready


def send_command(process: subprocess.Popen[str], command_type: str, **fields: Any) -> None:
    if process.stdin is None:
        raise RuntimeError("collection command pipe is not available")
    payload = {"type": command_type, **fields}
    process.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
    process.stdin.flush()


def oscilloscope_waveform(elapsed: float, width: int = 42) -> str:
    levels = "▁▂▃▄▅▆▇█"
    phase = elapsed * 8.0
    samples = []
    for index in range(width):
        x = index + phase
        signal = math.sin(x * 0.52) + 0.35 * math.sin(x * 1.31)
        normalized = max(0.0, min(1.0, (signal + 1.35) / 2.7))
        samples.append(levels[round(normalized * (len(levels) - 1))])
    return "".join(samples)


def recording_indicator_markup(episode_index: int, elapsed: float, bright: bool) -> str:
    total_seconds = max(0, int(elapsed))
    minutes, seconds = divmod(total_seconds, 60)
    if bright:
        indicator = tr("[bold bright_red]● REC[/bold bright_red]")
    else:
        indicator = tr("[dim red]○ REC[/dim red]")
    return tr(
        "{0}  [bold]EPISODE {1:04d}[/bold]  [bold]{2:02d}:{3:02d}[/bold]  [bold green]Enter[/bold green] finish  ·  [bold red]Esc/Ctrl+C[/bold red] exit  [bright_green]{4}[/bright_green][bold green]▌[/bold green]"
    ).format(indicator, episode_index, minutes, seconds, oscilloscope_waveform(elapsed, width=16))


def camera_runtime_markup(status: dict[str, Any]) -> str:
    fault = diagnostic(status.get("fault")) if status.get("fault") else None
    misses = int(status.get("consecutive_misses", 0))
    if fault:
        return tr("[bold red]CAM FAULT {0}[/bold red]").format(fault)
    if misses:
        return tr("[yellow]CAM MISS {0}/10[/yellow]").format(misses)
    fps = status.get("fps", {})
    values = "/".join(f"{float(value):.0f}" for value in fps.values()) or "—"
    skew = status.get("skew_ms")
    skew_label = "—" if skew is None else f"{float(skew):.1f} ms"
    return tr("[cyan]CAM {0} · {1}[/cyan]").format(values, skew_label)


def sync_indicator_markup(
    arm_error: float,
    gripper_error: float,
    elapsed: float,
    worst_joint: str = "",
) -> str:
    joint = f"[bold yellow]{worst_joint}[/bold yellow]  ·  " if worst_joint else ""
    return tr(
        "[bold cyan]↝ SYNC · NOT RECORDING[/bold cyan]  {0}arm {1:.3f} rad  ·  gripper {2:.3f}  ·  {3:04.1f}s  ·  [bold red]Esc/Ctrl+C[/bold red] SAFE HOLD"
    ).format(joint, arm_error, gripper_error, elapsed)


def runtime_status_markup(
    phase: str,
    *,
    episode_index: int,
    elapsed: float = 0.0,
    sync_arm_error: float = 0.0,
    sync_gripper_error: float = 0.0,
    sync_worst_joint: str = "",
    startup_message: str = "Initializing hardware",
    bright: bool = True,
    camera_status: dict[str, Any] | None = None,
    recording_rates: dict[str, Any] | None = None,
    trigger_values: list[float] | None = None,
    trigger_stage: str = "release_first",
) -> str:
    """Render every live control phase as one concise terminal line."""
    if phase == "initializing":
        return tr("[bold cyan]INIT[/bold cyan]  {0}").format(tr(startup_message))
    if phase == "syncing":
        return sync_indicator_markup(sync_arm_error, sync_gripper_error, elapsed, sync_worst_joint)
    if phase == "waiting_for_trigger":
        values = "/".join(f"{(1 - value) * 100:.0f}%" for value in (trigger_values or []))
        if language() == "zh":
            instruction = {"release_first": "请先松开所有主臂扳机",
                           "press": "请同时按到底所有主臂扳机",
                           "release": "请松开所有主臂扳机，等待对齐后录制"}[trigger_stage]
            return (f"[bold cyan]未录制[/bold cyan]  {instruction}"
                    f"  按下程度 {values}  ·  从臂慢速跟随  ·  Esc/Ctrl+C 退出")
        instruction = {"release_first": "Release all triggers first",
                       "press": "Fully squeeze all triggers",
                       "release": "Release all triggers; align before recording"}[trigger_stage]
        return (f"[bold cyan]NOT RECORDING[/bold cyan]  {instruction}"
                f"  squeeze {values}  ·  slow following  ·  Esc/Ctrl+C exit")
    if phase == "recording":
        base = recording_indicator_markup(episode_index, elapsed, bright)
        if recording_rates is not None:
            streams = recording_rates.get("streams", {})
            action = streams.get("/action/accepted", {}).get("measured_hz", 0)
            states = "/".join(f"{streams[f'/{side}/state']['measured_hz']:.0f}"
                              for side in ("left", "right", "third", "fourth")
                              if f"/{side}/state" in streams)
            cameras = "/".join(f"{streams[f'/cameras/{name}/rgb']['measured_hz']:.0f}"
                               for name in ("main", "left", "right", "third", "fourth")
                               if f"/cameras/{name}/rgb" in streams)
            missed = recording_rates.get("missed_deadlines", 0)
            label = "调度跳期" if language() == "zh" else "missed periods"
            return (base + f"\n[dim]ACTION {action:.1f} Hz · STATE {states or '--'} Hz"
                    f" · CAM {cameras or '--'} FPS · {label} {missed}[/dim]")
        if camera_status is not None:
            base += f"  ·  {camera_runtime_markup(camera_status)}"
        return base
    if phase == "stopping":
        return tr(
            "[bold yellow]STOPPING[/bold yellow]  EPISODE {0:04d}  ·  entering SAFE HOLD"
        ).format(episode_index)
    if phase == "review":
        return tr(
            "[bold yellow]REVIEW[/bold yellow]  EPISODE {0:04d}  ·  [bold green]s[/bold green] success  ·  [bold red]f[/bold red] fail  ·  [bold yellow]d[/bold yellow] discard"
        ).format(episode_index)
    if phase in {"ready", "next_ready"}:
        return tr(
            "[bold cyan]READY[/bold cyan]  EPISODE {0:04d}  ·  position GELLO  ·  [bold green]Enter[/bold green] sync + wait for triggers  ·  [bold red]Esc/Ctrl+C[/bold red] exit"
        ).format(episode_index)
    if phase == "exiting":
        return tr("[bold cyan]SAFE HOLD[/bold cyan]  exit requested · waiting for confirmation")
    if phase == "safe_hold":
        return tr("[bold cyan]SAFE HOLD[/bold cyan]  followers actively supported")
    raise ValueError(f"unknown runtime phase: {phase}")


def arming_prompt_markup() -> str:
    return tr(
        "[bold yellow]SAFETY CHECK[/bold yellow]  clear workspace · emergency stop ready  ·  [bold green]Enter[/bold green] arm + slow sync  ·  [bold red]Ctrl+C[/bold red] cancel"
    )


def episode_result_markup(outcome: str, path: str | None = None) -> str:
    if outcome == "success":
        return tr("[bold green]SAVED SUCCESS[/bold green]  {0}").format(path)
    if outcome == "fail":
        return tr("[bold red]SAVED FAIL[/bold red]  {0}").format(path)
    if outcome == "discard":
        return tr("[bold yellow]DISCARDED[/bold yellow]  episode not saved")
    raise ValueError(f"unknown episode outcome: {outcome}")


def render_session_duration(console, episode_paths: list[Path]) -> None:
    """Print totals for episodes committed during this teleop process."""
    mcap_paths = [p for p in episode_paths if p.suffix == ".mcap"]
    if mcap_paths:
        console.print(f"MCAP 已保存 {len(mcap_paths)} 条 · 独立数据流；质量检查：scripts/mcap inspect 文件路径")
    scan = inspect_episode_paths([p for p in episode_paths if p.suffix != ".mcap"])
    summary = scan.summary
    console.print(
        tr(
            "[bold cyan]SESSION DATA[/bold cyan]  {0} episodes · [green]{1} success[/green] / [yellow]{2} fail[/yellow] · {3} frames"
        ).format(
            summary.episode_count, summary.success_count, summary.fail_count, summary.total_steps
        )
    )
    console.print(
        tr("[bold]USABLE {0}[/bold]  ·  recorded span {1}").format(
            format_duration(summary.usable_seconds), format_duration(summary.recording_span_seconds)
        )
    )
    for error in scan.errors:
        console.print(tr("[red]DURATION ERROR[/red] {0}: {1}").format(error.path, error.message))


def live_status_text(markup: str) -> Text:
    status = Text.from_markup(markup, overflow="fold" if language() == "zh" else "ellipsis")
    status.no_wrap = language() == "en"
    return status


@contextmanager
def terminal_keys():
    """Read one key at a time while preserving Ctrl+C signal handling."""
    if not sys.stdin.isatty():
        yield
        return
    fd = sys.stdin.fileno()
    original = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, original)


def read_key(timeout: float = 0.2) -> str | None:
    if not sys.stdin.isatty():
        if os.environ.get("YAM_WEB_MANAGED") == "1":
            ready, _, _ = select.select([sys.stdin], [], [], timeout)
            if ready and sys.stdin.readline().strip() == "stop":
                return "escape"
        return None
    ready, _, _ = select.select([sys.stdin], [], [], timeout)
    if not ready:
        return None
    value = os.read(sys.stdin.fileno(), 1).decode(errors="ignore")
    if value in {"\r", "\n"}:
        return "enter"
    if value == "\x1b":
        return "escape"
    return value.lower()


def request_safe_stop(
    process, safety_client, reason: str, hold_ack: threading.Event | None = None
) -> None:
    """Ask collector to hold first; fall back to a direct RPC after 150 ms."""
    if process.poll() is None:
        try:
            send_command(process, "exit_task")
        except (BrokenPipeError, OSError):
            # The collector can exit between poll() and writing its command pipe.
            # Do not let that race bypass the immediate safety fallback.
            safety_client.safe_hold(reason)
            return
    deadline = time.monotonic() + 0.15
    acknowledged = False
    while time.monotonic() < deadline:
        if (hold_ack is not None and hold_ack.is_set()) or process.poll() is not None:
            acknowledged = True
            break
        time.sleep(0.01)
    if not acknowledged:
        safety_client.safe_hold(reason)


def apply_post_session_command(safety_client, session_id: str, command: str) -> str:
    """Apply an explicit post-session command; empty input leaves SAFE HOLD latched."""
    normalized = command.strip().lower()
    if normalized == "off":
        safety_client.disarm(session_id, "off")
        return "disarmed"
    if normalized in {"g", "gravity"}:
        safety_client.enter_gravity(session_id)
        return "gravity"
    return "safe_hold"


def post_session_menu_markup() -> str:
    return tr(
        "[green]\\[Enter][/green] SAFE HOLD · [magenta]\\[g][/magenta] gravity · [bold red]\\[o][/bold red] motors off"
    )


def prompt_post_session_action(
    console,
    safety_client,
    session_id: str,
    *,
    input_fn=input,
) -> str:
    """Read concise post-session hotkeys without weakening daemon confirmation."""
    console.print(post_session_menu_markup())
    while True:
        try:
            choice = input_fn(tr("Safety action: ")).strip().lower()
        except (EOFError, KeyboardInterrupt):
            return "safe_hold"
        if choice == "":
            return "safe_hold"
        if choice == "g":
            return apply_post_session_command(safety_client, session_id, "g")
        if choice == "o":
            console.print(
                tr(
                    "[bold red]Support every follower first. Press o again to disable motors.[/bold red]"
                )
            )
            try:
                confirmation = input_fn(tr("Confirm [o]: ")).strip().lower()
            except (EOFError, KeyboardInterrupt):
                return "safe_hold"
            if confirmation == "o":
                return apply_post_session_command(safety_client, session_id, "off")
            console.print(
                tr("[yellow]Motor disable cancelled · SAFE HOLD remains active.[/yellow]")
            )
            return "safe_hold"
        console.print(tr("[yellow]Invalid key · use Enter, g, or o.[/yellow]"))


def heartbeat_loop(
    client: SafetyClient,
    session_id: str,
    stop: threading.Event,
    failures: queue.Queue[str],
    interval_s: float = 0.2,
) -> None:
    while not stop.is_set():
        try:
            status = client.heartbeat("parent", session_id)
            if status and status.get("state") in {"fault", "disarmed"}:
                raise RuntimeError(
                    f"safety daemon entered {status['state'].upper()}: {status.get('reason')}"
                )
        except Exception as exc:
            failures.put(str(exc))
            return
        stop.wait(interval_s)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    raw = list(sys.argv[1:] if argv is None else argv)
    with terminal_language("zh"):
        language_parser = TerminalArgumentParser(add_help=False, allow_abbrev=False)
        language_parser.add_argument("--lang", choices=("zh", "en"), default="zh")
        language, _ = language_parser.parse_known_args(raw)
    with terminal_language(language.lang):
        return _parse_args(raw)


def announce_preview(console: Console, url: str, port: int) -> None:
    console.print(Text(tr("Camera preview: {0}").format(url)))
    if os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"):
        console.print(
            Text(
                tr(
                    "SSH: on your computer run {0}, then open the preview URL in your browser."
                ).format(f"ssh -N -L {port}:127.0.0.1:{port} <user>@<robot-host>")
            )
        )
    elif os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):

        def open_browser():
            try:
                # A retained stable page reconnects after service restart. Give
                # it one polling interval before opening another browser tab.
                from urllib.parse import urlparse
                if urlparse(url).path == "/":
                    import urllib.request
                    time.sleep(4.0)
                    try:
                        req = urllib.request.Request(url + "preview-viewer", headers={"X-YAM-Preview": "1"})
                        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                        with opener.open(req, timeout=1) as response:
                            if json.load(response).get("active"):
                                return
                    except Exception:
                        pass
                webbrowser.open(url, new=2)
            except Exception:
                pass  # The printed URL remains available when no browser is installed.

        threading.Thread(target=open_browser, daemon=True).start()


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = TerminalArgumentParser(
        add_help=False,
        allow_abbrev=False,
        prog="yam teleop",
        description=tr("Preflight and launch YAM two- or four-arm teleoperation."),
    )
    parser._optionals.title = tr("Options")
    parser.add_argument("-h", "--help", action="help", help=tr("show this help message and exit"))
    parser.add_argument(
        "--lang", choices=("zh", "en"), default="zh", help=tr("terminal language (default: zh)")
    )
    parser.add_argument(
        "--profile", type=Path, help=tr("machine profile (defaults to YAM_PROFILE/XDG)")
    )
    parser.add_argument("--left-config", type=Path)
    parser.add_argument("--right-config", type=Path)
    parser.add_argument("--third-config", type=Path)
    parser.add_argument("--fourth-config", type=Path)
    arm_selection = parser.add_mutually_exclusive_group()
    arm_selection.add_argument(
        "--arm-count",
        type=int,
        choices=(2, 4),
        help=tr("number of YAM arms to use (default: 2)"),
    )
    arm_selection.add_argument(
        "--two-pair",
        action="store_true",
        help=tr("deprecated alias for --arm-count 2"),
    )
    parser.add_argument("--guidance-bundle", type=Path, help="经 YAM 标定的布局包；录制时保存目标投影")
    parser.add_argument("--task-name", help=tr("task directory name for episode collection"))
    parser.add_argument("--check", action="store_true", help=tr("run preflight only"))
    parser.add_argument(
        "--camera-config",
        type=Path,
        help=tr("fixed RealSense rig configuration"),
    )
    cameras = parser.add_mutually_exclusive_group()
    cameras.add_argument(
        "--depth", action="store_true", help=tr("record aligned depth for all cameras")
    )
    cameras.add_argument("--no-cameras", action="store_true", help=tr("disable all camera capture"))
    parser.add_argument(
        "--no-preview", action="store_true", help=tr("disable browser camera preview")
    )
    parser.add_argument(
        "--preview-port", type=int, default=8765, help=tr("local preview port (default: 8765)")
    )
    parser.add_argument("--recording-format", choices=("hdf5", "mcap"), default="hdf5",
                        help="存储格式：hdf5（原模式）或 mcap（独立频率）")
    parser.add_argument("--control-hz", type=float, default=30,
                        help="MCAP 控制/action 目标频率，30..200 Hz")
    parser.add_argument("--state-hz", type=float, default=200,
                        help="MCAP state 质量目标频率，30..500 Hz；保存原生反馈，分批读取")
    parser.add_argument("-y", "--yes", action="store_true", help=tr("skip arming prompt"))
    parser.add_argument("--startup-timeout", type=float, default=60.0)
    parser.add_argument("--hardware-port", type=int)
    parser.add_argument("--safety-port", type=int)
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "yam-teleop",
    )
    args = parser.parse_args(argv)
    if not 30 <= args.control_hz <= 200 or not 30 <= args.state_hz <= 500:
        parser.error("control-hz 必须为 30..200，state-hz 必须为 30..500")
    if args.recording_format == "hdf5" and (args.control_hz != 30 or args.state_hz != 200):
        parser.error("独立频率参数需要 --recording-format mcap")
    if args.recording_format == "mcap":
        try:
            import av
            import mcap  # noqa: F401
            av.CodecContext.create("libx264", "w")
        except ImportError:
            parser.error("请先安装 MCAP：uv sync --frozen --extra yam --extra realsense --extra mcap")
    if not 1 <= args.preview_port <= 65535:
        parser.error(tr("preview port must be between 1 and 65535"))
    explicit_aux_configs = args.third_config is not None or args.fourth_config is not None
    if args.two_pair:
        args.arm_count = 2
    elif args.arm_count is None:
        # Preserve direct four-arm invocations that explicitly supplied the two
        # auxiliary configs, while making the ordinary launcher default bimanual.
        args.arm_count = 4 if explicit_aux_configs else 2
    if args.arm_count == 2 and explicit_aux_configs:
        parser.error(tr("third/fourth configs cannot be used with --arm-count 2"))
    profile = command_profile(parser, args.profile)
    assert profile is not None
    args.left_config = args.left_config or (profile.arms["left"].config_path)
    args.right_config = args.right_config or (profile.arms["right"].config_path)
    if args.arm_count == 4:
        third = profile.auxiliary_pairs.get("third")
        fourth = profile.auxiliary_pairs.get("fourth")
        args.third_config = args.third_config or (third.config_path if third else None)
        args.fourth_config = args.fourth_config or (fourth.config_path if fourth else None)
    if (args.third_config is None) != (args.fourth_config is None):
        parser.error(tr("third and fourth configs must be supplied together"))
    if args.arm_count == 4 and args.third_config is None:
        parser.error(
            tr(
                "--arm-count 4 requires calibrated third and fourth configs in the profile or via --third-config/--fourth-config"
            )
        )
    args.camera_config = args.camera_config or (
        profile.cameras.config_path if profile.cameras else None
    )
    args.hardware_port = (
        args.hardware_port if args.hardware_port is not None else (profile.network.hardware_port)
    )
    args.safety_port = (
        args.safety_port if args.safety_port is not None else (profile.network.safety_port)
    )
    args.data_root = profile.storage.root
    args.data_link = profile.storage.workspace_link
    effective_configs = {"left": args.left_config, "right": args.right_config}
    if (args.third_config is None) != (args.fourth_config is None):
        parser.error(tr("third and fourth configs must be supplied together"))
    if args.third_config is not None:
        effective_configs.update(third=args.third_config, fourth=args.fourth_config)
    if not args.no_cameras and args.camera_config is None:
        parser.error(tr("camera capture requires cameras.config in the profile or --camera-config"))
    args.machine_profile = profile
    validate_motion_configuration(
        parser,
        effective_configs,
        hardware_port=args.hardware_port,
        safety_port=args.safety_port,
        camera_path=None if args.no_cameras else args.camera_config,
    )
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    with terminal_language(args.lang):
        return _run_teleop(args)


def _run_teleop(args: argparse.Namespace) -> int:
    # Validate optional projection bundles before any camera or motor startup.
    if args.guidance_bundle is not None:
        if args.recording_format != "mcap" or args.no_cameras:
            raise ValueError("Guidance requires MCAP and main camera")
        from gello.data_utils.real2sim_guidance import session as guidance_session
        guidance_session(args.guidance_bundle)
    console = Console()
    storage_failures = [
        result for result in storage_checks(args.machine_profile) if result.status == "FAIL"
    ]
    if storage_failures:
        for result in storage_failures:
            console.print(
                tr("[red]Storage preflight failed:[/red] {0}").format(diagnostic(result.summary))
            )
        return 2
    try:
        configs = {
            "left": load_config(args.left_config.resolve()),
            "right": load_config(args.right_config.resolve()),
        }
        if args.third_config is not None:
            configs["third"] = load_config(args.third_config.resolve())
            configs["fourth"] = load_config(args.fourth_config.resolve())
        left_cfg = configs["left"]
        server_port = args.hardware_port
        arms = [inspect_arm(name, config) for name, config in configs.items()]
        config_errors = validate_configs(configs, server_port)
        camera_config = None
        camera_statuses: list[CameraStatus] = []
        camera_errors: list[str] = []
        if not args.no_cameras:
            camera_config = load_camera_rig_config(args.camera_config.resolve())
            camera_statuses, camera_errors = inspect_cameras(
                camera_config, enumerate_realsense_devices()
            )
    except (
        ImportError,
        OSError,
        RuntimeError,
        KeyError,
        TypeError,
        ValueError,
        yaml.YAMLError,
    ) as exc:
        console.print(tr("[bold red]Preflight failed:[/bold red] {0}").format(diagnostic(exc)))
        return 2

    rate_hz = args.control_hz if args.recording_format == "mcap" else int(left_cfg.get("hz", 30))
    render_preflight(console, arms, rate_hz, server_port)
    if camera_statuses:
        render_camera_preflight(console, camera_statuses, depth_enabled=args.depth)
    elif args.no_cameras:
        console.print(tr("[yellow]CAMERAS OFF[/yellow]  robot-only debugging mode"))
    errors = [*config_errors, *camera_errors]
    for arm in arms:
        if not arm.serial_exists:
            errors.append(
                tr("{0} GELLO is not connected: {1}").format(label(arm.name), arm.leader_port)
            )
        elif not arm.serial_accessible:
            errors.append(
                tr(
                    "{0} GELLO permission denied; add {1} to the dialout group, then log in again"
                ).format(label(arm.name), os.environ.get("USER", "this user"))
            )
        if arm.serial_exists and arm.serial_accessible and arm.latency_ms != 1:
            latency_path = serial_latency_path(arm.leader_port)
            if arm.latency_ms is None or latency_path is None:
                errors.append(tr("{0} GELLO FTDI latency_timer could not be read").format(arm.name))
            else:
                errors.append(
                    tr(
                        "{0} GELLO FTDI latency is {1} ms; set it to 1 ms: echo 1 | sudo tee {2}"
                    ).format(label(arm.name), arm.latency_ms, latency_path)
                )
        if arm.serial_exists and arm.serial_accessible and arm.leader_error:
            errors.append(
                tr(
                    "{0} GELLO bus check failed: {1}; verify leader power and the cable to Dynamixel ID 1"
                ).format(label(arm.name), arm.leader_error)
            )
        if not arm.can_ok:
            errors.append(
                tr("{0} CAN {1} is {2}").format(label(arm.name), arm.can_channel, arm.can_state)
            )
        if arm.bitrate != 1_000_000:
            errors.append(tr("{0} CAN bitrate must be 1 Mbps").format(arm.name))

    if errors:
        for error in errors:
            console.print(f"[red]✗[/red] {error}")
        return 2
    camera_health = None
    if camera_config is not None:
        try:
            with console.status(tr("Checking sustained RealSense streams")):
                camera_health = run_camera_stream_check(camera_config, depth_enabled=args.depth)
        except Exception as exc:
            console.print(
                tr("[bold red]Camera stream check failed:[/bold red] {0}").format(diagnostic(exc))
            )
            console.print(
                tr(
                    "[yellow]Capture is blocked before arming.[/yellow] Restore every camera stream, then run [bold]scripts/teleop --check[/bold]."
                )
            )
            return 2
    if args.check:
        if camera_config is not None:
            assert camera_health is not None
            fps = "/".join(
                f"{camera_health[name].measured_fps:.0f}" for name in camera_config.cameras
            )
            console.print(tr("[green]✓ Camera streams stable[/green] · {0} FPS").format(fps))
        console.print(tr("[green]✓ Preflight passed[/green]"))
        return 0

    try:
        task_name = args.task_name
        if task_name is None:
            task_name = input(tr("Task name: ")).strip()
        task_name = validate_task_name(task_name)
        if args.data_link is not None:
            data_root = ensure_data_link(args.data_link.parent, args.data_root)
        else:
            data_root = args.data_root
        catalog_factory = EpisodeWriter
        if args.recording_format == "mcap":
            from gello.data_utils.mcap_recording import McapCatalog
            catalog_factory = McapCatalog
            console.print(f"MCAP H.264 · action 目标 {args.control_hz:g} Hz · state 目标 {args.state_hz:g} Hz（原生反馈）· 相机独立采集")
        episode_catalog = catalog_factory(data_root, task_name, rate_hz, arm_names=tuple(configs))
        if os.environ.get("YAM_WEB_MANAGED") == "1" and episode_catalog.incomplete_recordings:
            raise RuntimeError("该任务有未完成记录，请先在工作台恢复记录或选择新任务；原文件已保留")
        for incomplete in list(episode_catalog.incomplete_recordings):
            while True:
                answer = (
                    input(
                        tr("Incomplete episode {0}: [f] save as fail · [d] discard: ").format(
                            incomplete.name
                        )
                    )
                    .strip()
                    .lower()
                )
                if answer in {"f", "d"}:
                    break
            if answer == "f":
                recovered = episode_catalog.recover_incomplete(incomplete, "fail")
                console.print(tr("[yellow]Recovered as fail:[/yellow] {0}").format(recovered))
            else:
                episode_catalog.recover_incomplete(incomplete, "discard")
                console.print(
                    tr("[yellow]Discarded incomplete:[/yellow] {0}").format(incomplete.name)
                )
        for warning in getattr(episode_catalog, "publication_warnings", []):
            console.print(f"[yellow]{diagnostic(warning)}[/yellow]")
        console.print(
            tr(
                "[bold cyan]TASK {0}[/bold cyan]  ·  next [bold green]{1:04d}[/bold green]  ·  [dim]{2}[/dim]"
            ).format(task_name, episode_catalog.next_episode_index, data_root)
        )
    except (EOFError, OSError, RuntimeError, ValueError) as exc:
        console.print(tr("[bold red]Data preflight failed:[/bold red] {0}").format(diagnostic(exc)))
        return 2

    if not args.yes:
        console.print()
        console.print(arming_prompt_markup())
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            console.print(tr("[yellow]Cancelled.[/yellow]"))
            return 130

    args.log_dir.expanduser().mkdir(parents=True, exist_ok=True)
    log_path = args.log_dir.expanduser() / datetime.now().strftime("teleop-%Y%m%d-%H%M%S.log")
    daemon_log_path = args.log_dir.expanduser() / datetime.now().strftime(
        "safety-%Y%m%d-%H%M%S.log"
    )
    workspace_root = REPO_ROOT
    env = os.environ.copy()
    old_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        f"{workspace_root}{os.pathsep}{old_pythonpath}" if old_pythonpath else str(workspace_root)
    )
    env["PYTHONUNBUFFERED"] = "1"

    expected_channels = {name: str(config["robot"]["channel"]) for name, config in configs.items()}
    try:
        with console.status(tr("Verifying watchdogs · preparing safety daemon")):
            daemon_status, daemon_process = ensure_safety_daemon(
                repo_root=REPO_ROOT,
                python=sys.executable,
                left_config=args.left_config.resolve(),
                right_config=args.right_config.resolve(),
                third_config=(args.third_config.resolve() if args.third_config else None),
                fourth_config=(args.fourth_config.resolve() if args.fourth_config else None),
                channels=expected_channels,
                hardware_port=args.hardware_port,
                safety_port=args.safety_port,
                log_path=daemon_log_path,
                env=env,
                timeout_s=args.startup_timeout,
            )
    except RuntimeError as exc:
        console.print(
            tr("[bold red]Safety daemon unavailable:[/bold red] {0}").format(diagnostic(exc))
        )
        console.print(tr("Safety log: {0}").format(daemon_log_path))
        return 2
    if daemon_process is None:
        console.print(
            tr("[cyan]Attached to safety daemon PID {0} · state {1}[/cyan]").format(
                daemon_status.get("pid"), label(daemon_status.get("state"))
            )
        )
    else:
        console.print(
            tr("[green]✓ {0} motor watchdogs verified at 400 ms[/green]").format(7 * len(configs))
        )

    session_id = uuid.uuid4().hex
    safety_client = SafetyClient(port=args.safety_port, timeout_ms=1000)
    heartbeat_client = SafetyClient(port=args.safety_port, timeout_ms=500)
    safety_client.begin_session(session_id)
    heartbeat_failures: queue.Queue[str] = queue.Queue()
    heartbeat_stop = threading.Event()
    heartbeat_thread = threading.Thread(
        target=heartbeat_loop,
        args=(
            heartbeat_client,
            session_id,
            heartbeat_stop,
            heartbeat_failures,
        ),
        name="parent_heartbeat",
        daemon=True,
    )
    heartbeat_thread.start()

    command = [
        sys.executable,
        str(REPO_ROOT / "experiments/collect_bimanual.py"),
        "--left-config-path",
        str(args.left_config.resolve()),
        "--right-config-path",
        str(args.right_config.resolve()),
        "--quiet",
        "--task-name",
        task_name,
        "--data-dir",
        str(data_root),
        "--hardware-port",
        str(args.hardware_port),
        "--safety-port",
        str(args.safety_port),
        "--safety-session",
        session_id,
    ]
    command.extend(["--recording-format", args.recording_format,
                    "--control-hz", str(args.control_hz), "--state-hz", str(args.state_hz)])
    if args.third_config is not None:
        command.extend(["--third-config-path", str(args.third_config.resolve())])
        command.extend(["--fourth-config-path", str(args.fourth_config.resolve())])
    if args.no_cameras:
        command.append("--no-cameras")
    else:
        command.extend(["--camera-config-path", str(args.camera_config.resolve())])
        if not args.no_preview:
            command.extend(["--preview-port", str(args.preview_port), "--preview-lang", args.lang])
        if args.depth:
            command.append("--camera-depth")
    if args.guidance_bundle is not None:
        if args.recording_format != "mcap" or args.no_cameras:
            raise ValueError("Guidance requires MCAP and main camera")
        env["YAM_GUIDANCE_BUNDLE"] = str(args.guidance_bundle.resolve())
    else:
        env.pop("YAM_GUIDANCE_BUNDLE", None)
    process = subprocess.Popen(
        command,
        cwd=REPO_ROOT,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        start_new_session=True,
    )
    assert process.stdout is not None
    messages: queue.Queue[str] = queue.Queue()
    history: list[str] = []
    reader = threading.Thread(
        target=output_reader, args=(process.stdout, log_path, messages), daemon=True
    )
    reader.start()

    startup_message = tr("Initializing collector")
    startup_complete = False
    deadline = time.monotonic() + args.startup_timeout
    phase = "initializing"
    episode_index = episode_catalog.next_episode_index
    recording_started_at: float | None = None
    sync_arm_error = 0.0
    sync_gripper_error = 0.0
    sync_elapsed = 0.0
    sync_worst_joint = ""
    hold_ack = threading.Event()
    camera_live_status: dict[str, Any] | None = None
    recording_rates: dict[str, Any] | None = None
    trigger_values: list[float] = []
    trigger_stage = "release_first"
    committed_episode_paths: list[Path] = []

    def handle_events(lines: list[str]) -> None:
        nonlocal phase, episode_index, recording_started_at, startup_complete
        nonlocal startup_message
        nonlocal sync_arm_error, sync_gripper_error, sync_elapsed, sync_worst_joint
        nonlocal camera_live_status, recording_rates
        nonlocal trigger_values, trigger_stage
        for line in lines:
            event = parse_event(line)
            if event is None:
                if re.search(r"\b(error|failed|bus-off)\b", line, re.IGNORECASE):
                    console.print(tr("[red]ERROR[/red] {0}").format(diagnostic(line)))
                continue
            if os.environ.get("YAM_WEB_MANAGED") == "1":
                print("YAM_WEB_EVENT " + json.dumps(event), flush=True)
            event_type = event.get("type")
            if event_type == "preview_ready":
                announce_preview(console, str(event["url"]), int(event["port"]))
            elif event_type == "preview_unavailable":
                console.print(
                    Text(
                        tr("Camera preview unavailable; capture continues: {0}").format(
                            event.get("message", "")
                        )
                    )
                )
            elif event_type == "episode_started":
                recording_rates = None
                hold_ack.clear()
                episode_index = int(event["episode_index"])
                recording_started_at = time.monotonic()
                startup_complete = True
                phase = "recording"
            elif event_type == "waiting_for_trigger":
                startup_complete = True
                phase = "waiting_for_trigger"
                trigger_values = []
                trigger_stage = "release_first"
            elif event_type == "trigger_progress":
                trigger_values = event.get("values", [])
                trigger_stage = event.get("stage", "release_first")
            elif event_type == "awaiting_label":
                phase = "review"
            elif event_type == "episode_committed":
                console.print(episode_result_markup(str(event["outcome"]), event["path"]))
                committed_episode_paths.append(Path(str(event["path"])))
            elif event_type == "episode_discarded":
                console.print(episode_result_markup("discard"))
            elif event_type == "ready":
                episode_index = int(event["episode_index"])
                startup_complete = True
                phase = "ready"
            elif event_type == "next_ready":
                hold_ack.clear()
                episode_index = int(event["episode_index"])
                startup_complete = True
                phase = "next_ready"
            elif event_type == "sync_started":
                sync_arm_error = 0.0
                sync_gripper_error = 0.0
                sync_elapsed = 0.0
                sync_worst_joint = ""
                phase = "syncing"
            elif event_type == "sync_progress":
                sync_arm_error = float(event.get("max_arm_error", 0.0))
                sync_gripper_error = float(event.get("max_gripper_error", 0.0))
                sync_elapsed = float(event.get("elapsed", 0.0))
                side = label(event.get("worst_side", ""))
                joint = event.get("worst_joint")
                sync_worst_joint = (
                    tr("{0} J{1}").format(side, int(joint)) if side and joint is not None else ""
                )
                phase = "syncing"
            elif event_type == "sync_failed":
                console.print(
                    tr("[bold red]Synchronization failed:[/bold red] {0}").format(
                        diagnostic(event.get("message"))
                    )
                )
            elif event_type == "cameras_ready":
                camera_live_status = {
                    "fps": event.get("fps", {}),
                    "skew_ms": None,
                    "consecutive_misses": 0,
                }
                startup_message = tr("Cameras streaming · preparing control")
            elif event_type == "camera_sync_ok":
                camera_live_status = {
                    "fps": event.get("fps", {}),
                    "skew_ms": event.get("skew_ms"),
                    "consecutive_misses": 0,
                }
            elif event_type == "recording_rates":
                recording_rates = event
            elif event_type == "camera_sync_miss":
                current = dict(camera_live_status or {})
                current["consecutive_misses"] = int(event.get("consecutive", 1))
                camera_live_status = current
            elif event_type == "camera_fault":
                camera_live_status = {
                    "fault": str(event.get("message", "unknown")),
                    "consecutive_misses": int(event.get("consecutive", 10)),
                }
            elif event_type == "safe_hold_active":
                hold_ack.set()
                phase = "safe_hold"
            elif event_type in {"error", "leader_fault", "safety_fault", "save_cleanup_pending"}:
                if event_type in {"leader_fault", "safety_fault"}:
                    phase = "fault"
                console.print(
                    tr("[bold red]Collection error:[/bold red] {0}").format(
                        diagnostic(event.get("message"))
                    )
                )
            elif event_type == "diagnostics_ready":
                console.print(tr("Diagnostic log: {0}").format(event["path"]))
            elif event_type == "diagnostics_warning":
                console.print(tr("Diagnostic logging incomplete: {0}; dropped={1}").format(event.get("message", ""), event.get("dropped", 0)))
            elif event_type == "save_failed":
                phase = "review"
                console.print(
                    tr("[bold red]Save failed:[/bold red] {0} · choose s/f/d to retry").format(
                        diagnostic(event.get("message"))
                    )
                )

    try:
        with (
            terminal_keys(),
            Live(
                live_status_text(
                    runtime_status_markup(
                        phase,
                        episode_index=episode_index,
                        startup_message=startup_message,
                    )
                ),
                console=console,
                refresh_per_second=5,
                transient=True,
            ) as runtime_live,
        ):
            while process.poll() is None:
                lines = drain(messages, history)
                handle_events(lines)
                startup_message, _ = startup_label(lines, startup_message)
                try:
                    heartbeat_error = heartbeat_failures.get_nowait()
                except queue.Empty:
                    heartbeat_error = None
                if heartbeat_error is not None:
                    raise RuntimeError(f"parent heartbeat failed: {heartbeat_error}")
                if not startup_complete and time.monotonic() >= deadline:
                    raise TimeoutError("hardware initialization timed out")

                elapsed = 0.0
                if phase == "recording" and recording_started_at is not None:
                    elapsed = time.monotonic() - recording_started_at
                elif phase == "syncing":
                    elapsed = sync_elapsed
                runtime_live.update(
                    live_status_text(
                        runtime_status_markup(
                            phase,
                            episode_index=episode_index,
                            elapsed=elapsed,
                            sync_arm_error=sync_arm_error,
                            sync_gripper_error=sync_gripper_error,
                            sync_worst_joint=sync_worst_joint,
                            startup_message=startup_message,
                            bright=int(elapsed * 3) % 2 == 0,
                            camera_status=camera_live_status,
                            recording_rates=recording_rates,
                            trigger_values=trigger_values,
                            trigger_stage=trigger_stage,
                        )
                    ),
                    refresh=True,
                )

                try:
                    if os.environ.get("YAM_WEB_MANAGED") == "1":
                        time.sleep(0.2)
                    key = read_key(0.2)
                except KeyboardInterrupt:
                    request_safe_stop(
                        process,
                        safety_client,
                        "operator pressed Ctrl+C",
                        hold_ack,
                    )
                    phase = "exiting"
                    continue

                if key == "escape":
                    request_safe_stop(process, safety_client, "operator pressed Escape", hold_ack)
                    phase = "exiting"
                    continue
                if phase == "recording":
                    if key == "enter":
                        send_command(process, "finish_episode")
                        phase = "stopping"
                elif phase == "review" and key in {"s", "f", "d"}:
                    outcome = {"s": "success", "f": "fail", "d": "discard"}[key]
                    send_command(process, "label", outcome=outcome)
                elif phase in {"ready", "next_ready"}:
                    if key == "enter":
                        send_command(process, "start_episode")
        if not startup_complete:
            raise RuntimeError("collector exited before reaching a ready state")
        reader.join(timeout=1)
    except (RuntimeError, TimeoutError) as exc:
        console.print(f"[bold red]{exc}[/bold red]")
        try:
            safety_client.safe_hold(f"orchestrator exception: {exc}")
        except Exception as hold_exc:
            console.print(
                tr("[bold red]SAFE HOLD RPC failed:[/bold red] {0}").format(diagnostic(hold_exc))
            )
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        reader.join(timeout=1)
        heartbeat_stop.set()
        heartbeat_thread.join(timeout=1)
        heartbeat_client.close()
        safety_client.close()
        render_session_duration(console, committed_episode_paths)
        console.print(tr("Log: {0}").format(log_path))
        return 1

    final_status = None
    try:
        final_status = safety_client.safe_hold("collection process stopped")
    except Exception as exc:
        console.print(tr("[bold red]SAFE HOLD RPC failed:[/bold red] {0}").format(diagnostic(exc)))
    heartbeat_stop.set()
    heartbeat_thread.join(timeout=1)
    heartbeat_client.close()

    if process.returncode not in (0, -signal.SIGINT):
        console.print(
            tr("[red]Teleoperation stopped unexpectedly (exit {0}).[/red]").format(
                process.returncode
            )
        )
        for line in history[-8:]:
            console.print(f"  {line}")
        console.print(tr("Log: {0}").format(log_path))
        exit_code = process.returncode or 1
    else:
        exit_code = 0

    if (
        final_status
        and final_status.get("state") == "safe_hold"
        and final_status.get("hold_confirmed")
    ):
        console.print(
            tr(
                "\n[bold cyan]SAFE HOLD ACTIVE[/bold cyan]\nFollowers remain actively supported by the safety daemon."
            )
        )
    else:
        state = "unknown" if not final_status else final_status.get("state", "unknown")
        reason = tr("status unavailable") if not final_status else final_status.get("reason", "")
        console.print(
            tr(
                "\n[bold red]ACTIVE HOLD NOT CONFIRMED[/bold red] · state={0}: {1}\nThe 400 ms motor watchdog/damping is the fallback. Physically support every selected arm."
            ).format(label(state), diagnostic(reason))
        )
        exit_code = exit_code or 1
    render_session_duration(console, committed_episode_paths)
    try:
        final_state = ("safe_hold" if os.environ.get("YAM_WEB_MANAGED") == "1"
                       else prompt_post_session_action(console, safety_client, session_id))
        if final_state == "safe_hold" and final_status is not None:
            final_state = str(final_status.get("state", final_state))
    except Exception as exc:
        console.print(
            tr("[bold red]Safety action rejected:[/bold red] {0}").format(diagnostic(exc))
        )
        final_state = "safe_hold"
        exit_code = exit_code or 1
    safety_client.close()

    if final_state == "disarmed":
        console.print(tr("[yellow]Followers explicitly disabled.[/yellow]"))
    elif final_state == "gravity":
        console.print(tr("[cyan]Gravity compensation remains active in the safety daemon.[/cyan]"))
    elif final_state == "safe_hold":
        console.print(
            tr("[green]SAFE HOLD remains active.[/green] Re-run scripts/teleop to attach.")
        )
    else:
        console.print(
            tr("[bold red]Follower safety state is {0}, not confirmed HOLD.[/bold red]").format(
                label(final_state)
            )
        )
    console.print(tr("Log: {0} · Safety log: {1}").format(log_path, daemon_log_path))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
