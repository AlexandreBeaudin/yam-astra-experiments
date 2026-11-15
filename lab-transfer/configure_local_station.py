#!/usr/bin/env python3
"""Adapt a transfer snapshot to a lab-verified station profile. No hardware I/O."""
import argparse
import json
from pathlib import Path
import shutil
import socket
import yaml

ROOT=Path(__file__).resolve().parents[1]


def configure(profile_path, expected_host, apply=False, *, left_channel='can_yam_b', right_channel='can_yam_a'):
    if not (ROOT/'SOURCE_SNAPSHOT.json').exists():
        raise RuntimeError('Run this helper inside a prepared transfer snapshot, not the working YAM3 experiment directory.')
    actual=socket.gethostname().lower().split('.')[0]
    if expected_host.lower()!=actual:
        raise ValueError(f'Expected host {expected_host!r} does not match this computer {actual!r}')
    profile_path=profile_path.expanduser().resolve()
    p=yaml.safe_load(profile_path.read_text())
    if p.get('schema_version')!=1:
        raise ValueError('Expected a schema_version: 1 YAM station profile')
    def resolved(value):
        path=Path(value).expanduser()
        return (path if path.is_absolute() else profile_path.parent/path).resolve(strict=True)
    channels={}
    for side in ('left','right'):
        path=resolved(p['arms'][side]);p['arms'][side]=str(path)
        channels[side]=yaml.safe_load(path.read_text())['robot']['channel']
    expected_channels={'left':left_channel,'right':right_channel}
    if left_channel == right_channel or channels != expected_channels:
        raise ValueError(f'This runner requires lab-verified mapping {expected_channels}; profile has {channels}. Do not relabel hardware to bypass this check. Have the lab adapt and verify the runner mapping.')
    p['cameras']['config']=str(resolved(p['cameras']['config']))
    if p['network'].get('host') not in ('127.0.0.1','localhost'):
        raise ValueError('The destination profile must use local station services')
    if (p['network'].get('hardware_port'),p['network'].get('safety_port'))!=(6001,6002):
        raise ValueError('This documented setup expects local ports 6001/6002; adapt and verify nonstandard ports separately')
    if p['arms']['left']==p['arms']['right']:
        raise ValueError('Left and right configs must be distinct')
    runner=ROOT/'integrations/inspect_robots/astra_run.py'
    text=runner.read_text()
    if expected_channels != {'left':'can_yam_b','right':'can_yam_a'}:
        import ast
        tree=ast.parse(text)
        guards=[node for node in ast.walk(tree) if isinstance(node,ast.Compare)
                and isinstance(node.left,ast.Name) and node.left.id=='channels'
                and len(node.ops)==1 and isinstance(node.ops[0],ast.NotEq)]
        if not any(ast.literal_eval(node.comparators[0]) == expected_channels for node in guards):
            raise ValueError('Runner channel guard must first be adapted to the verified station mapping')
    old="if socket.gethostname().lower().split('.')[0] != 'rhos-nuc-1':"
    new=f"if socket.gethostname().lower().split('.')[0] != {actual!r}:"
    if old not in text and new not in text:
        raise ValueError('Runner hostname guard differs; review it manually instead of patching blindly')
    patched=text.replace(old,new).replace("station operations must run on rhos-nuc-1",f'station operations must run on {actual}')
    report=dict(host=actual,profile_source=str(profile_path),arm_configs=p['arms'],channels=channels,
                camera_config=p['cameras']['config'],network=p['network'],apply=apply)
    print(json.dumps(report,indent=2))
    if apply:
        # Preserve first local backup; never modify the station's original profile.
        backup=runner.with_suffix('.py.bak')
        if not backup.exists():shutil.copy2(runner,backup)
        target=ROOT/'profiles/shurui-mcap.yaml'
        if target.exists() and not target.with_suffix('.yaml.bak').exists():
            shutil.copy2(target,target.with_suffix('.yaml.bak'))
        target.parent.mkdir(exist_ok=True)
        target.write_text(yaml.safe_dump(p,sort_keys=False))
        runner.write_text(patched)
        print('Local profile and exact hostname guard configured. No services started; no robot or camera connection.')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile',type=Path,required=True)
    parser.add_argument('--expected-host',required=True)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--left-channel',default='can_yam_b',help='Lab-verified physical left follower channel; runner must already match')
    parser.add_argument('--right-channel',default='can_yam_a',help='Lab-verified physical right follower channel; runner must already match')
    args=parser.parse_args()
    configure(args.profile,args.expected_host,args.apply,left_channel=args.left_channel,right_channel=args.right_channel)
