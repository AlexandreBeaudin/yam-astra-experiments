#!/usr/bin/env python3
"""Inspect or export MCAP recordings without opening robot hardware."""

import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="MCAP 质量检查与 HDF5 导出")
    sub = parser.add_subparsers(dest="command", required=True)
    inspect = sub.add_parser("inspect")
    inspect.add_argument("path", type=Path)
    export = sub.add_parser("export-hdf5")
    export.add_argument("path", type=Path)
    export.add_argument("--output-root", type=Path, required=True)
    export.add_argument("--task-name", required=True)
    export.add_argument("--outcome", choices=("success", "fail"), default="fail")
    args = parser.parse_args()
    try:
        from gello.data_utils import mcap_tools

        result = (
            mcap_tools.inspect(args.path)
            if args.command == "inspect"
            else mcap_tools.export_hdf5(args.path, args.output_root, args.task_name, args.outcome)
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except (ImportError, ValueError, OSError, RuntimeError) as exc:
        parser.exit(2, f"MCAP: {exc}\n")


if __name__ == "__main__":
    main()
