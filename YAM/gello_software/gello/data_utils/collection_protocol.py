"""Line-oriented control protocol between the terminal UI and robot process."""

from __future__ import annotations

import json
import queue
import sys
from typing import Any, Callable, Dict, Optional, TextIO


EVENT_PREFIX = "YAM_EVENT "


def emit_event(event: Dict[str, Any], stream: Optional[TextIO] = None) -> None:
    output = sys.stdout if stream is None else stream
    print(f"{EVENT_PREFIX}{json.dumps(event, separators=(',', ':'))}", file=output, flush=True)


def parse_event(line: str) -> Optional[Dict[str, Any]]:
    if not line.startswith(EVENT_PREFIX):
        return None
    try:
        event = json.loads(line[len(EVENT_PREFIX) :])
    except json.JSONDecodeError:
        return None
    return event if isinstance(event, dict) else None


def read_commands(
    stream: TextIO,
    commands: "queue.Queue[Dict[str, Any]]",
    on_error: Callable[[str], None],
) -> None:
    for line in iter(stream.readline, ""):
        try:
            command = json.loads(line)
            if not isinstance(command, dict):
                raise ValueError("command must be a JSON object")
            commands.put(command)
        except (json.JSONDecodeError, ValueError) as exc:
            on_error(str(exc))

