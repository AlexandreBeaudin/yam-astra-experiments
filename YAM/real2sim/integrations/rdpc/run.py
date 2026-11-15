#!/usr/bin/env python3
"""Foreground supervisor. The algorithm gets an explicit credential-free environment."""
import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import yaml


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    os.umask(0o077)
    config_path = Path(args.config).resolve()
    config = yaml.safe_load(config_path.read_text())
    source = Path(os.environ['RDPC_SOURCE']).resolve()
    bridge = source / 'rdpc/worker/worker.py'
    if not bridge.is_file():
        raise SystemExit('RDPC_SOURCE must point to the pinned RDPC checkout')
    if '.invalid' in config['scheduler_url'] or '.invalid' in config['public_url']:
        raise SystemExit('Replace example Scheduler/callback URLs before registration')
    token = os.environ.get(config['token_env'], '')
    if len(token) < 32:
        raise SystemExit('Worker token environment variable missing or too short')
    if set(config['task_types']) != {'tel.placement_strategy', 'tel.simulation_asset', 'tel.placement_supplement'} or config['capacity'] != 1:
        raise SystemExit('This mock profile requires all three TEL types, capacity=1')
    if any(t['health_url'] != 'http://127.0.0.1:8852/health' or t.get('enabled') is not True for t in config['task_types'].values()):
        raise SystemExit('This profile uses subsystem health port 8852')
    db = Path(config['database'])
    if not db.is_absolute():
        raise SystemExit('Use an absolute database path')
    db.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Whitelist, do not merely remove known credential names from inherited env.
    safe = dict(PATH='/usr/bin:/bin', LANG='C.UTF-8', PYTHONUNBUFFERED='1', RDPC_SOURCE=str(source))
    worker_env = dict(safe, **{config['token_env']: token})
    children = []
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, stop)
    try:
        children.append(subprocess.Popen([sys.executable, str(Path(__file__).with_name('subsystem.py')),
                                          '--database', str(db)], env=safe))
        children.append(subprocess.Popen([sys.executable, str(bridge), '--config', str(config_path)], env=worker_env))
        while not stopping and all(p.poll() is None for p in children):
            time.sleep(.5)
        if not stopping:
            raise SystemExit('Worker or subsystem exited; both stopped. Inspect safe process logs.')
    finally:
        # Stop the algorithm first, then let the bridge unregister its session.
        for child in children:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()


if __name__ == '__main__':
    main()
