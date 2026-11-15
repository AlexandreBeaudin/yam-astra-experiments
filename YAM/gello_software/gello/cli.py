"""Installed command-line entry point for the YAM toolkit."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from rich.console import Console
from rich.table import Table

from gello.doctor import exit_code, json_report, run_doctor
from gello.profile import (
    ProfileError,
    load_profile,
    register_auxiliary_pair,
    resolve_profile_path,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="yam", description="YAM robot toolkit")
    parser.add_argument("--version", action="version", version="yam-robot-toolkit 0.2.0")
    commands = parser.add_subparsers(dest="command", required=True)

    doctor = commands.add_parser(
        "doctor", help="strictly read-only workstation diagnostics"
    )
    doctor.add_argument("--profile", type=Path)
    mode = doctor.add_mutually_exclusive_group()
    mode.add_argument("--deep", action="store_true", help="also open RGB camera streams")
    mode.add_argument("--offline", action="store_true", help="skip all hardware checks")
    doctor.add_argument("--json", action="store_true", help="stable machine-readable output")

    profile = commands.add_parser("profile", help="inspect machine profiles")
    profile_commands = profile.add_subparsers(dest="profile_command", required=True)
    for name in ("show", "validate"):
        command = profile_commands.add_parser(name)
        command.add_argument("--profile", type=Path)
        command.add_argument("--json", action="store_true")
    register = profile_commands.add_parser(
        "register-auxiliary",
        help="register a connected but non-active leader/follower pair",
    )
    register.add_argument("--profile", type=Path)
    register.add_argument("--name", required=True)
    register.add_argument("--leader-port", required=True)
    register.add_argument("--can-channel", required=True)
    register.add_argument("--can-adapter-serial", required=True)
    register.add_argument("--json", action="store_true")
    for name, help_text in (
        ("teleop", "run two-arm or four-arm teleoperation and collection"),
        ("replay", "replay a recorded episode"),
        ("visualize", "render an episode MP4"),
        ("return-zero", "move both followers to configured zero"),
    ):
        command = commands.add_parser(name, help=help_text, add_help=False)
        command.add_argument("arguments", nargs=argparse.REMAINDER)
    return parser


def _profile_payload(profile) -> dict:
    return {
        "schema_version": profile.schema_version,
        "name": profile.name,
        "path": str(profile.path),
        "arms": {
            side: {
                "config": str(arm.config_path),
                "can": arm.can_channel,
                "leader": str(arm.leader_port),
                "hz": arm.control_hz,
            }
            for side, arm in profile.arms.items()
        },
        "auxiliary_pairs": {
            name: {
                "leader": str(pair.leader_port),
                "can": pair.can_channel,
                "can_adapter_serial": pair.can_adapter_serial,
                "config": str(pair.config_path) if pair.config_path else None,
            }
            for name, pair in profile.auxiliary_pairs.items()
        },
        "cameras": (
            {
                "config": str(profile.cameras.config_path),
                "required": profile.cameras.required,
            }
            if profile.cameras
            else None
        ),
        "storage": {
            "root": str(profile.storage.root),
            "workspace_link": (
                str(profile.storage.workspace_link)
                if profile.storage.workspace_link
                else None
            ),
            "min_free_gb": profile.storage.min_free_gb,
        },
        "network": {
            "host": profile.network.host,
            "hardware_port": profile.network.hardware_port,
            "safety_port": profile.network.safety_port,
        },
        "safety": {
            "profile": profile.safety.profile,
            "watchdog_ms": profile.safety.watchdog_ms,
            "motors_per_arm": profile.safety.motors_per_arm,
        },
    }


def _doctor(args: argparse.Namespace) -> int:
    profile = load_profile(args.profile)
    results = run_doctor(profile, deep=args.deep, offline=args.offline)
    if args.json:
        print(json_report(profile, results))
        return exit_code(results)
    console = Console()
    table = Table(title=f"YAM Doctor · {profile.name}", box=None)
    table.add_column("STATUS", style="bold")
    table.add_column("CHECK")
    table.add_column("RESULT")
    colors = {"PASS": "green", "WARN": "yellow", "FAIL": "red", "SKIP": "dim"}
    for result in results:
        color = colors[result.status]
        table.add_row(
            f"[{color}]{result.status}[/{color}]",
            result.check,
            result.summary,
        )
    console.print(table)
    failures = sum(result.status == "FAIL" for result in results)
    warnings = sum(result.status == "WARN" for result in results)
    console.print(
        f"[{'red' if failures else 'green'}]"
        f"{failures} failed[/] · [yellow]{warnings} warnings[/]"
    )
    return exit_code(results)


def main(argv: Sequence[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    passthrough = raw[0] if raw else None
    if passthrough == "teleop":
        from scripts.run_bimanual_teleop import main as command

        return command(raw[1:])
    if passthrough == "replay":
        from scripts.replay import main as command

        return command(raw[1:])
    if passthrough == "visualize":
        from scripts.visualize import main as command

        return command(raw[1:])
    if passthrough == "return-zero":
        from scripts.return_zero import main as command

        return command(raw[1:])
    args = _parser().parse_args(raw)
    try:
        if args.command == "doctor":
            return _doctor(args)
        if args.profile_command == "register-auxiliary":
            profile = register_auxiliary_pair(
                args.profile,
                name=args.name,
                leader_port=args.leader_port,
                can_channel=args.can_channel,
                can_adapter_serial=args.can_adapter_serial,
            )
        else:
            profile = load_profile(args.profile)
        payload = _profile_payload(profile)
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        elif args.profile_command == "validate":
            Console().print(
                f"[green]✓[/green] Profile valid · {profile.name} · {profile.path}"
            )
        else:
            print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    except ProfileError as exc:
        if getattr(args, "json", False):
            print(
                json.dumps(
                    {
                        "schema_version": 1,
                        "error": "profile_error",
                        "message": str(exc),
                        "path": str(resolve_profile_path(getattr(args, "profile", None))),
                    },
                    sort_keys=True,
                )
            )
        else:
            Console(stderr=True).print(f"[red]Profile error:[/red] {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
