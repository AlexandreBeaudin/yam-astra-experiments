#!/usr/bin/env python3
"""Summarize usable and recorded duration below an episode directory."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from rich.console import Console
from rich.table import Table

from gello.data_utils.episode_duration import (
    DurationScan,
    format_duration,
    scan_episode_directory,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="recursively summarize completed YAM HDF5 episode durations"
    )
    parser.add_argument("directory", type=Path)
    parser.add_argument(
        "--details",
        action="store_true",
        help="list every valid episode in addition to the totals",
    )
    return parser


def render_scan(console: Console, scan: DurationScan, *, details: bool) -> None:
    if details and scan.episodes:
        table = Table(title="Episode durations", box=None)
        table.add_column("OUTCOME")
        table.add_column("FRAMES", justify="right")
        table.add_column("USABLE", justify="right")
        table.add_column("RECORDED", justify="right")
        table.add_column("FILE")
        for episode in scan.episodes:
            style = "green" if episode.outcome == "success" else "yellow"
            table.add_row(
                f"[{style}]{episode.outcome.upper()}[/{style}]",
                str(episode.num_steps),
                format_duration(episode.usable_seconds),
                format_duration(episode.recording_span_seconds),
                str(episode.path),
            )
        console.print(table)

    summary = scan.summary
    console.print(
        "[bold cyan]DATA DURATION[/bold cyan]  "
        f"{summary.episode_count} episodes · "
        f"[green]{summary.success_count} success[/green] / "
        f"[yellow]{summary.fail_count} fail[/yellow] · "
        f"{summary.total_steps} frames"
    )
    console.print(
        f"[bold]USABLE {format_duration(summary.usable_seconds)}[/bold]  ·  "
        f"recorded span {format_duration(summary.recording_span_seconds)}"
    )
    for error in scan.errors:
        console.print(
            f"[red]INVALID[/red] {error.path}: {error.message}"
        )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    console = Console()
    try:
        scan = scan_episode_directory(args.directory)
    except ValueError as exc:
        console.print(f"[red]Duration scan failed:[/red] {exc}")
        return 2
    render_scan(console, scan, details=args.details)
    return 1 if scan.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
