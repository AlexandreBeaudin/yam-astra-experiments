#!/usr/bin/env python3
"""Inspect or explicitly change the persistent YAM safety daemon state."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gello.safety.rpc import SafetyClient


def _session(client: SafetyClient, status: dict) -> str:
    session_id = status.get("session_id")
    if session_id:
        return str(session_id)
    session_id = f"safety-cli-{uuid.uuid4().hex}"
    if status.get("state") == "fault":
        return session_id
    client.begin_session(session_id)
    return session_id


def main() -> int:
    parser = argparse.ArgumentParser(description="YAM follower safety controls")
    parser.add_argument("command", choices=("status", "hold", "gravity", "off"))
    parser.add_argument("--port", type=int, default=6002)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    client = SafetyClient(port=args.port)
    try:
        status = client.status()
        if args.command == "hold":
            status = client.safe_hold("operator requested HOLD from scripts/safety")
        elif args.command == "gravity":
            if status["state"] != "safe_hold":
                raise RuntimeError("gravity is only available from SAFE_HOLD")
            status = client.enter_gravity(_session(client, status))
        elif args.command == "off":
            if not sys.stdin.isatty():
                raise RuntimeError("off requires an interactive terminal")
            confirmation = input("Type exactly 'off' to disable follower motors: ").strip()
            if confirmation != "off":
                print("Cancelled; follower state was not changed.")
                return 1
            status = client.disarm(_session(client, status), confirmation)
        if args.json:
            print(json.dumps(status, indent=2, sort_keys=True))
        else:
            print(f"state={status['state'].upper()}  hold_confirmed={status.get('hold_confirmed', False)}")
            print(f"reason={status['reason']}")
            health = status.get("hardware_health", {})
            for side, values in health.get("sides", {}).items():
                print(
                    f"{side.upper()}: thread={values.get('control_thread_alive')} "
                    f"feedback={values.get('feedback_age_s')}s "
                    f"loop={values.get('comm_freq_hz')}Hz "
                    f"temp_mos={values.get('max_temp_mos_c')}C "
                    f"temp_rotor={values.get('max_temp_rotor_c')}C"
                )
        return 0
    except Exception as exc:
        print(f"safety command failed: {exc}", file=sys.stderr)
        return 2
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
