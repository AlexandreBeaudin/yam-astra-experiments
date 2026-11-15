#!/usr/bin/env python3
"""Dedicated process entry point for safety-daemon-backed collection.

The implementation currently reuses launch_yaml's configuration construction,
while keeping the public generic launcher and the collection process as distinct
entry points. This prevents the terminal supervisor from accidentally selecting
the legacy direct-hardware path.
"""

from __future__ import annotations

import sys

from experiments.launch_yaml import main


if __name__ == "__main__":
    if "--task-name" not in sys.argv or "--safety-session" not in sys.argv:
        raise SystemExit(
            "collect_bimanual requires --task-name and --safety-session; "
            "use experiments/launch_yaml.py for simulation or legacy operation"
        )
    main()
