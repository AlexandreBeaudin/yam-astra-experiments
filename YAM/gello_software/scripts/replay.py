#!/usr/bin/env python3
"""Replay one recorded two-arm or four-arm YAM episode through the safety daemon."""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable

import numpy as np
from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.text import Text

from gello.command_profile import command_profile, validate_motion_configuration
from gello.data_utils.episode_writer import (
    ensure_data_link,
    validate_task_name,
)
from gello.data_utils.replay import (
    EpisodeSummary,
    discover_episodes,
    discover_tasks,
    load_episode,
    page_items,
    playback_deadlines,
    resolve_episode_choice,
    resolve_task_choice,
    run_replay,
    validate_episode_joint_limits,
)
from gello.safety.confirmation import require_confirmed_hold
from gello.safety.daemon_client import ensure_safety_daemon
from gello.safety.return_zero import arm_indices_for_size
from gello.safety.rpc import SafetyClient
from gello.zmq_core.robot_node import ZMQClientRobot
from scripts.return_zero import load_config

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_TARGET = Path("/mnt/sdb/YAM/data")


def choose_task(
    console: Console,
    data_root: Path,
    task_name: str | None,
    *,
    input_fn: Callable[[str], str] = input,
) -> str:
    tasks = discover_tasks(data_root)
    if task_name is not None:
        validated = validate_task_name(task_name)
        if not any(task.name == validated for task in tasks):
            raise ValueError(f"task has no completed episodes: {validated}")
        return validated
    if not tasks:
        raise ValueError(f"no completed replay tasks found in {data_root}")
    table = Table(title="Replay tasks", box=None)
    table.add_column("#", justify="right", style="bold green")
    table.add_column("TASK", style="bold cyan")
    table.add_column("SUCCESS", justify="right", style="green")
    table.add_column("FAIL", justify="right", style="red")
    table.add_column("LATEST", justify="right")
    for index, task in enumerate(tasks, start=1):
        table.add_row(
            str(index),
            task.name,
            str(task.success_count),
            str(task.fail_count),
            f"{task.latest_episode:04d}",
        )
    console.print(table)
    while True:
        try:
            return resolve_task_choice(tasks, input_fn("Task (# or name): ")).name
        except ValueError as exc:
            console.print(f"[yellow]{exc}[/yellow]")


def _episode_table(rows: list[EpisodeSummary], page: int, pages: int) -> Table:
    table = Table(title=f"Episodes · page {page}/{pages}", box=None)
    table.add_column("#", justify="right", style="bold green")
    table.add_column("EPISODE", style="bold")
    table.add_column("RESULT")
    table.add_column("FRAMES", justify="right")
    table.add_column("DURATION", justify="right")
    for index, episode in enumerate(rows, start=1):
        outcome = (
            "[green]SUCCESS[/green]"
            if episode.outcome == "success"
            else "[red]FAIL[/red]"
        )
        if episode.valid:
            frames = str(episode.num_steps)
            duration = f"{float(episode.duration):.2f}s"
        else:
            outcome = "[bold red]INVALID[/bold red]"
            frames = "—"
            duration = "—"
        table.add_row(
            str(index), f"{episode.episode_index:04d}", outcome, frames, duration
        )
    return table


def choose_episode(
    console: Console,
    data_root: Path,
    task_name: str,
    *,
    input_fn: Callable[[str], str] = input,
) -> EpisodeSummary:
    episodes = discover_episodes(data_root, task_name)
    if not episodes:
        raise ValueError(f"task has no completed episodes: {task_name}")
    page = 1
    while True:
        rows, page, pages = page_items(episodes, page)
        console.print(_episode_table(rows, page, pages))
        choice = input_fn("Episode (#, 0001, n/p): ").strip().lower()
        if choice == "n" and page < pages:
            page += 1
            continue
        if choice == "p" and page > 1:
            page -= 1
            continue
        try:
            selected = resolve_episode_choice(
                episodes if choice.isdigit() and len(choice) >= 4 else rows,
                choice,
            )
        except ValueError as exc:
            console.print(f"[yellow]{exc}[/yellow]")
            continue
        if not selected.valid:
            console.print(f"[bold red]Invalid episode:[/bold red] {selected.error}")
            continue
        return selected


def prompt_after_replay(
    console: Console, *, input_fn: Callable[[str], str] = input
) -> str:
    console.print(
        "[bold cyan]REPLAY COMPLETE · SAFE HOLD[/bold cyan]  ·  "
        "[bold green]Enter[/bold green] keep pose  ·  "
        "[bold yellow]z[/bold yellow] return zero"
    )
    while True:
        try:
            choice = input_fn("After replay: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return "hold"
        if choice == "":
            return "hold"
        if choice == "z":
            return "zero"
        console.print("[yellow]Use Enter or z.[/yellow]")


def replay_status_markup(
    phase: str, index: int, total: int, elapsed: float, speed: float
) -> str:
    progress = 100.0 if total <= 0 else min(100.0, 100.0 * index / total)
    if phase == "move_to_start":
        return f"[bold yellow]MOVE TO START[/bold yellow]  {progress:5.1f}%"
    if phase == "replay":
        return (
            f"[bold magenta]REPLAY[/bold magenta]  {index}/{total}  ·  "
            f"{elapsed:06.2f}s  ·  {speed:.2f}×  ·  "
            "[bold red]Ctrl+C[/bold red] SAFE HOLD"
        )
    if phase == "return_zero":
        return f"[bold yellow]RETURN ZERO[/bold yellow]  {progress:5.1f}%"
    if phase == "hold":
        return "[bold cyan]SAFE HOLD[/bold cyan]  followers actively supported"
    raise ValueError(f"unknown replay phase: {phase}")


def _status_text(markup: str) -> Text:
    text = Text.from_markup(markup, overflow="ellipsis")
    text.no_wrap = True
    return text


class ReplayLiveDisplay:
    """Own one Rich Live line at a time across replay UI phases."""

    def __init__(self, console: Console, speed: float, *, live_factory=Live):
        self.console = console
        self.speed = float(speed)
        self.live_factory = live_factory
        self.live = None

    def start(self, phase: str) -> None:
        self.stop()
        self.live = self.live_factory(
            _status_text(replay_status_markup(phase, 0, 1, 0.0, self.speed)),
            console=self.console,
            transient=True,
            auto_refresh=False,
        )
        self.live.start(refresh=True)

    def update(self, phase: str, index: int, total: int, elapsed: float) -> None:
        if self.live is None:
            return
        self.live.update(
            _status_text(
                replay_status_markup(
                    phase, index, total, elapsed, self.speed
                )
            ),
            refresh=True,
        )

    def stop(self) -> None:
        if self.live is None:
            return
        live = self.live
        self.live = None
        live.stop()


def choose_after_replay(
    console: Console,
    display: ReplayLiveDisplay,
    *,
    input_fn: Callable[[str], str] = input,
) -> str:
    """Leave the dynamic line before input, then start a fresh zero line."""
    display.update("hold", 1, 1, 0.0)
    display.stop()
    choice = prompt_after_replay(console, input_fn=input_fn)
    if choice == "zero":
        display.start("return_zero")
    return choice


def active_safety_log_path(
    candidate: Path, daemon_process: object | None
) -> Path | None:
    """Return a log only when this invocation created the daemon writing it."""
    return candidate if daemon_process is not None else None


def request_safe_hold_preserving(
    safety, reason: str, original: BaseException
) -> BaseException:
    """Request HOLD without allowing its failure to erase the first failure."""
    if safety is None:
        return original
    try:
        require_confirmed_hold(safety.safe_hold(reason))
    except BaseException as hold_failure:
        return BaseExceptionGroup(
            "replay and SAFE HOLD both failed", [original, hold_failure]
        )
    return original


def _failure_details(failure: BaseException) -> str:
    if isinstance(failure, BaseExceptionGroup):
        return " · ".join(_failure_details(item) for item in failure.exceptions)
    message = str(failure)
    return f"{type(failure).__name__}: {message}" if message else type(failure).__name__


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="yam replay",
        description="Replay a recorded two-arm or four-arm YAM episode"
    )
    parser.add_argument("task_name", nargs="?", help="optional task to browse")
    parser.add_argument("--profile", type=Path, help="machine profile (defaults to YAM_PROFILE/XDG)")
    parser.add_argument(
        "--speed", type=float, default=1.0, help="replay factor from 0.1 to 1.0"
    )
    parser.add_argument(
        "--left-config", type=Path
    )
    parser.add_argument(
        "--right-config", type=Path
    )
    parser.add_argument("--third-config", type=Path)
    parser.add_argument("--fourth-config", type=Path)
    parser.add_argument("--hardware-port", type=int)
    parser.add_argument("--safety-port", type=int)
    parser.add_argument("--startup-timeout", type=float, default=60.0)
    parser.add_argument("-y", "--yes", action="store_true", help="skip motion confirmation")
    args = parser.parse_args(argv)
    profile = command_profile(parser, args.profile)
    assert profile is not None
    args.left_config = args.left_config or (
        profile.arms["left"].config_path
    )
    args.right_config = args.right_config or (
        profile.arms["right"].config_path
    )
    third = profile.auxiliary_pairs.get("third")
    fourth = profile.auxiliary_pairs.get("fourth")
    args.third_config = args.third_config or (
        third.config_path if third is not None else None
    )
    args.fourth_config = args.fourth_config or (
        fourth.config_path if fourth is not None else None
    )
    args.hardware_port = args.hardware_port if args.hardware_port is not None else (
        profile.network.hardware_port
    )
    args.safety_port = args.safety_port if args.safety_port is not None else (
        profile.network.safety_port
    )
    args.data_target = profile.storage.root
    args.data_link = (
        profile.storage.workspace_link
    )
    effective_configs = {"left": args.left_config, "right": args.right_config}
    if (args.third_config is None) != (args.fourth_config is None):
        parser.error("third and fourth configs must be supplied together")
    if args.third_config is not None:
        effective_configs.update(third=args.third_config, fourth=args.fourth_config)
    validate_motion_configuration(
        parser, effective_configs,
        hardware_port=args.hardware_port, safety_port=args.safety_port,
    )
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    console = Console()
    safety = None
    robot = None
    try:
        playback_deadlines(np.array([0.0]), args.speed)
        if args.data_link is not None:
            data_root = ensure_data_link(args.data_link.parent, args.data_target)
        else:
            data_root = args.data_target
        task_name = choose_task(console, data_root, args.task_name)
        selected = choose_episode(console, data_root, task_name)
        episode = load_episode(selected.path)
    except (EOFError, KeyboardInterrupt):
        console.print("[yellow]Cancelled before hardware startup.[/yellow]")
        return 130
    except (OSError, RuntimeError, ValueError) as exc:
        console.print(f"[bold red]Replay data error:[/bold red] {exc}")
        return 2

    duration = float(episode.summary.duration or 0.0)
    console.print(
        f"[bold cyan]TASK {task_name}[/bold cyan]  ·  "
        f"[bold]EPISODE {episode.summary.episode_index:04d}[/bold]  ·  "
        f"{episode.summary.outcome.upper()}  ·  {episode.summary.num_steps} frames  ·  "
        f"{duration:.2f}s → {duration / args.speed:.2f}s at "
        f"[bold magenta]{args.speed:.2f}×[/bold magenta]"
    )

    log_path = None
    try:
        arm_count = episode.actions.shape[1] // 7
        config_paths = {
            "left": args.left_config,
            "right": args.right_config,
        }
        if arm_count == 4:
            if args.third_config is None or args.fourth_config is None:
                raise ValueError(
                    "four-arm replay requires --third-config and --fourth-config "
                    "or configured profile auxiliary pairs"
                )
            config_paths.update(
                {"third": args.third_config, "fourth": args.fourth_config}
            )
        configs = {
            name: load_config(path.resolve())
            for name, path in config_paths.items()
        }
        channels = {
            name: str(config["robot"]["channel"])
            for name, config in configs.items()
        }
        workspace = REPO_ROOT
        env = os.environ.copy()
        env["PYTHONPATH"] = (
            f"{workspace}{os.pathsep}{env['PYTHONPATH']}"
            if env.get("PYTHONPATH")
            else str(workspace)
        )
        env["PYTHONUNBUFFERED"] = "1"
        state_dir = Path(
            env.get("XDG_STATE_HOME", Path.home() / ".local/state")
        ) / "yam-teleop"
        candidate_log_path = state_dir / datetime.now().strftime(
            "replay-safety-%Y%m%d-%H%M%S.log"
        )
        with console.status("Preparing safety daemon"):
            _daemon_status, daemon_process = ensure_safety_daemon(
                repo_root=REPO_ROOT,
                python=sys.executable,
                left_config=args.left_config.resolve(),
                right_config=args.right_config.resolve(),
                third_config=(
                    args.third_config.resolve() if arm_count == 4 else None
                ),
                fourth_config=(
                    args.fourth_config.resolve() if arm_count == 4 else None
                ),
                channels=channels,
                hardware_port=args.hardware_port,
                safety_port=args.safety_port,
                log_path=candidate_log_path,
                env=env,
                timeout_s=args.startup_timeout,
            )
        log_path = active_safety_log_path(candidate_log_path, daemon_process)
        safety = SafetyClient(port=args.safety_port)
        validate_episode_joint_limits(episode, safety.status())
        robot = ZMQClientRobot(port=args.hardware_port)
        current = np.asarray(robot.get_joint_state(), dtype=float)
        arm_indices = arm_indices_for_size(episode.actions.shape[1])
        max_start_move = float(
            np.abs(current - episode.start_qpos)[arm_indices].max()
        )
        console.print(
            f"[bold yellow]START MOVE[/bold yellow]  max arm displacement "
            f"{max_start_move:.3f} rad"
        )
        if not args.yes:
            console.print(
                "[bold yellow]SAFETY CHECK[/bold yellow]  "
                "clear workspace · emergency stop ready  ·  "
                "[bold green]Enter[/bold green] start  ·  "
                "[bold red]Ctrl+C[/bold red] cancel"
            )
            input()

        display = ReplayLiveDisplay(console, args.speed)
        display.start("move_to_start")

        def progress(phase: str, index: int, total: int, elapsed: float) -> None:
            display.update(phase, index, total, elapsed)

        def choose_after() -> str:
            return choose_after_replay(console, display)

        try:
            result = run_replay(
                robot,
                safety,
                uuid.uuid4().hex,
                episode,
                speed=args.speed,
                choose_after=choose_after,
                on_progress=progress,
            )
        finally:
            display.stop()
        if result == "zero":
            console.print("[bold green]REPLAY COMPLETE[/bold green]  zero pose · SAFE HOLD")
        else:
            console.print("[bold green]REPLAY COMPLETE[/bold green]  final pose · SAFE HOLD")
        if log_path is not None:
            console.print(f"[dim]Safety log: {log_path}[/dim]")
        return 0
    except KeyboardInterrupt as exc:
        failure = request_safe_hold_preserving(
            safety, "replay cancelled by operator", exc
        )
        if isinstance(failure, BaseExceptionGroup):
            console.print(
                "\n[bold red]Replay cancellation and SAFE HOLD failed:[/bold red] "
                f"{_failure_details(failure)}"
            )
        elif safety is None:
            console.print("\n[yellow]Replay cancelled; SAFE HOLD not confirmed.[/yellow]")
        else:
            console.print("\n[bold cyan]SAFE HOLD[/bold cyan]  replay cancelled")
        return 130
    except Exception as exc:
        failure = request_safe_hold_preserving(
            safety,
            f"replay error: {type(exc).__name__}: {exc}",
            exc,
        )
        console.print(
            f"[bold red]Replay failed:[/bold red] {_failure_details(failure)}"
        )
        if log_path is not None:
            console.print(f"[dim]Safety log: {log_path}[/dim]")
        return 1
    finally:
        if robot is not None:
            robot.close()
        if safety is not None:
            safety.close()


if __name__ == "__main__":
    raise SystemExit(main())
