#!/usr/bin/env python3
"""Create a synchronized joint-timeline and three-camera episode MP4."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.text import Text

from gello.command_profile import command_profile
from gello.data_utils.episode_writer import (
    EPISODE_PATTERN,
    TASK_NAME_PATTERN,
)
from gello.data_utils.visualization import (
    default_output_path,
    load_visualization_episode,
    render_episode_video,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = REPO_ROOT.parent / "data"


@dataclass(frozen=True)
class EpisodePath:
    index: int
    outcome: str
    path: Path


def _task_names(data_root: Path) -> list[str]:
    root = Path(data_root)
    if not root.is_dir():
        raise ValueError(f"data directory does not exist: {root}")
    names = []
    for task in root.iterdir():
        if not task.is_dir() or TASK_NAME_PATTERN.fullmatch(task.name) is None:
            continue
        if any(
            any((task / outcome).glob("*.hdf5"))
            for outcome in ("success", "fail")
        ):
            names.append(task.name)
    return sorted(names)


def choose_task(
    console: Console,
    data_root: Path,
    task_name: str | None,
    *,
    input_fn: Callable[[str], str] = input,
) -> str:
    tasks = _task_names(data_root)
    if not tasks:
        raise ValueError(f"no completed episodes found in {data_root}")
    if task_name is not None:
        if task_name not in tasks:
            raise ValueError(f"task has no completed episodes: {task_name}")
        return task_name
    table = Table(title="Visualization tasks", box=None)
    table.add_column("#", justify="right", style="bold green")
    table.add_column("TASK", style="bold cyan")
    for row, name in enumerate(tasks, start=1):
        table.add_row(str(row), name)
    console.print(table)
    while True:
        choice = input_fn("Task (# or name): ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(tasks):
            return tasks[int(choice) - 1]
        if choice in tasks:
            return choice
        console.print(f"[yellow]Unknown task selection: {choice}[/yellow]")


def _episode_paths(data_root: Path, task_name: str) -> list[EpisodePath]:
    task_dir = Path(data_root) / task_name
    found: dict[int, EpisodePath] = {}
    for outcome in ("success", "fail"):
        directory = task_dir / outcome
        if not directory.is_dir():
            continue
        for path in directory.glob("*.hdf5"):
            match = EPISODE_PATTERN.fullmatch(path.name)
            if match is None:
                continue
            index = int(match.group(1))
            if index in found:
                raise ValueError(f"duplicate episode number: {index:04d}")
            found[index] = EpisodePath(index, outcome, path)
    return [found[index] for index in sorted(found, reverse=True)]


def choose_episode_path(
    console: Console,
    data_root: Path,
    task_name: str,
    episode: str | None,
    *,
    input_fn: Callable[[str], str] = input,
) -> Path:
    episodes = _episode_paths(data_root, task_name)
    if not episodes:
        raise ValueError(f"task has no completed episodes: {task_name}")
    if episode is not None:
        if not episode.isdigit():
            raise ValueError("--episode must be a numeric episode such as 0001")
        wanted = int(episode)
        for item in episodes:
            if item.index == wanted:
                return item.path
        raise ValueError(f"episode does not exist: {wanted:04d}")

    table = Table(title=f"Episodes · {task_name}", box=None)
    table.add_column("#", justify="right", style="bold green")
    table.add_column("EPISODE", style="bold")
    table.add_column("RESULT")
    for row, item in enumerate(episodes, start=1):
        result = (
            "[green]SUCCESS[/green]"
            if item.outcome == "success"
            else "[red]FAIL[/red]"
        )
        table.add_row(str(row), f"{item.index:04d}", result)
    console.print(table)
    while True:
        choice = input_fn("Episode (# or 0001): ").strip()
        if choice.isdigit() and len(choice) >= 4:
            wanted = int(choice)
            for item in episodes:
                if item.index == wanted:
                    return item.path
        elif choice.isdigit() and 1 <= int(choice) <= len(episodes):
            return episodes[int(choice) - 1].path
        console.print(f"[yellow]Unknown episode selection: {choice}[/yellow]")


def resolve_output_path(source: Path, output: Path | None) -> Path:
    if output is not None:
        return Path(output)
    try:
        return default_output_path(source)
    except ValueError as exc:
        raise ValueError(
            "--output is required when --input is outside the standard data layout"
        ) from exc


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="yam visualize",
        description="Render a synchronized YAM joint and three-camera MP4"
    )
    parser.add_argument("task_name", nargs="?", help="optional task to browse")
    parser.add_argument("--profile", type=Path, help="machine profile (defaults to YAM_PROFILE/XDG)")
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--episode", help="episode number, for example 0007")
    parser.add_argument("--input", type=Path, help="direct HDF5 episode path")
    parser.add_argument(
        "--cameras",
        nargs=3,
        metavar=("CAM1", "CAM2", "CAM3"),
        help="three cameras in display order",
    )
    parser.add_argument("--fps", type=float, help="output FPS; defaults to control_hz")
    parser.add_argument("--output", type=Path, help="output MP4 path")
    parser.add_argument("--force", action="store_true", help="replace an existing MP4")
    args = parser.parse_args(argv)
    if args.input is not None and (args.task_name is not None or args.episode is not None):
        parser.error("--input cannot be combined with TASK_NAME or --episode")
    if args.episode is not None and args.task_name is None and args.input is None:
        parser.error("--episode requires TASK_NAME")
    profile = command_profile(parser, args.profile, required=False)
    args.data_root = args.data_root or (
        profile.storage.root if profile else DATA_ROOT
    )
    return args


def _progress_text(index: int, total: int, timestamp: float) -> Text:
    percent = 100.0 if total <= 0 else 100.0 * index / total
    return Text.from_markup(
        f"[bold magenta]RENDER[/bold magenta]  {index}/{total}  ·  "
        f"{percent:5.1f}%  ·  {timestamp:06.2f}s"
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    console = Console()
    try:
        if args.input is not None:
            source = args.input.expanduser().resolve()
            if not source.is_file():
                raise ValueError(f"input HDF5 does not exist: {source}")
        else:
            task_name = choose_task(console, args.data_root, args.task_name)
            source = choose_episode_path(
                console, args.data_root, task_name, args.episode
            ).resolve()
        output = resolve_output_path(source, args.output)
        with load_visualization_episode(
            source, selected_cameras=args.cameras
        ) as episode:
            fps = episode.control_hz if args.fps is None else float(args.fps)
            cameras = (
                ", ".join(episode.camera_names)
                if episode.camera_names
                else "none · joint data only"
            )
            console.print(
                f"[bold cyan]TASK {episode.task_name}[/bold cyan]  ·  "
                f"[bold]EPISODE {episode.episode_index:04d}[/bold]  ·  "
                f"{episode.outcome.upper()}  ·  {episode.qpos.shape[0]} samples  ·  "
                f"{episode.duration:.2f}s"
            )
            console.print(f"CAMERAS  {cameras}")
            console.print(f"OUTPUT   {output}  ·  {fps:g} FPS  ·  1920×1080")
            if not episode.camera_names:
                console.print(
                    "[yellow]No RGB frames found; generating joint-only MP4.[/yellow]"
                )
            live = Live(
                _progress_text(0, 1, 0.0),
                console=console,
                transient=True,
                auto_refresh=False,
            )
            live.start(refresh=True)

            def progress(index: int, total: int, timestamp: float) -> None:
                live.update(_progress_text(index, total, timestamp), refresh=True)

            try:
                frame_count = render_episode_video(
                    episode,
                    output,
                    fps=fps,
                    force=args.force,
                    progress=progress,
                )
            finally:
                live.stop()
        console.print(
            f"[bold green]VIDEO READY[/bold green]  {frame_count} frames  ·  {output}"
        )
        return 0
    except (EOFError, KeyboardInterrupt):
        console.print("\n[yellow]Visualization cancelled.[/yellow]")
        return 130
    except Exception as exc:
        console.print(f"[bold red]Visualization failed:[/bold red] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
