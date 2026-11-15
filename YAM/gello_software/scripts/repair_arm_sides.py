#!/usr/bin/env python3
"""Repair this station's reversed arm labels, with verified MCAP replacement.

No hardware is opened. Default is a read-only inventory. Only the known old
CAN/leader/camera mapping and the explicitly supported bimanual schema qualify.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
from pathlib import Path
import shutil
import tempfile
from datetime import datetime
from zoneinfo import ZoneInfo

from mcap.reader import make_reader
from mcap.writer import Writer
import yaml

FIX_ID = 'yam-physical-arm-sides-v1'
SIDES = {'/left/state': '/right/state', '/right/state': '/left/state'}
TOPICS = {'/session', '/left/state', '/right/state', '/action/requested',
          '/action/accepted', '/cameras/main/rgb', '/cameras/left/rgb', '/cameras/right/rgb'}
OLD = {'left': ('can_follower_r', 'FTC2FNQP'), 'right': ('can_follower_l', 'FTBSX7UI')}
SERIALS = {'main': '408122072021', 'left': '260322274929', 'right': '260422272011'}


def encode(value):
    return json.dumps(value, separators=(',', ':'), allow_nan=False).encode()


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(4 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def session(path):
    with Path(path).open('rb') as f:
        messages = list(make_reader(f, validate_crcs=True).iter_messages(topics=['/session']))
    if len(messages) != 1:
        raise ValueError(f'{path}: expected one session')
    return json.loads(messages[0][2].data)['data']


def check_mapping(configs):
    if set(configs) != set(OLD):
        raise ValueError('Only the verified two-arm rig is supported')
    for name, (can, leader) in OLD.items():
        cfg = configs[name]
        if cfg['robot']['channel'] != can or leader not in cfg['agent']['port']:
            raise ValueError(f'Unexpected {name} CAN/leader mapping; do not guess')


def check_session(s):
    if s.get('arm_side_repair'):
        raise ValueError('Already repaired; refusing a second swap')
    if s.get('schema_version') != 2 or s['arm_names'] != ['left', 'right']:
        raise ValueError('Unsupported recording schema or arm order')
    check_mapping(s['arm_configs'])
    if {k: v['serial'] for k, v in s['cameras'].items()} != SERIALS:
        raise ValueError('Camera identities differ from the verified rig')


def swap_vector(value):
    if not isinstance(value, list) or len(value) != 14:
        raise ValueError('Expected bimanual 14-element action')
    return value[7:] + value[:7]


def transform(topic, raw):
    if topic not in TOPICS:
        raise ValueError(f'Unknown topic requires explicit review: {topic}')
    if topic in SIDES:
        return SIDES[topic], raw  # Feedback bytes and all timestamps remain exact.
    if topic.startswith('/cameras/'):
        return topic, raw  # Never decode/re-encode the video.
    payload = json.loads(raw)
    data = payload['data']
    if topic == '/session':
        check_session(data)
        data['arm_configs'] = {'left': data['arm_configs']['right'],
                               'right': data['arm_configs']['left']}
        data['arm_side_repair'] = {
            'id': FIX_ID, 'definition': 'physical side matching wrist camera names',
            'original_arm_to_corrected_arm': {'left': 'right', 'right': 'left'},
            'action_order': ['left', 'right'], 'joints_per_arm': 7,
            'cameras_and_timestamps_unchanged': True,
        }
    else:
        allowed = {'position', 'command_id'} if topic == '/action/requested' else {
            'position', 'requested_position', 'limited', 'command_id'}
        if set(data) - allowed:
            raise ValueError(f'Unknown action fields: {set(data) - allowed}')
        data['position'] = swap_vector(data['position'])
        if 'requested_position' in data:
            data['requested_position'] = swap_vector(data['requested_position'])
    return topic, encode(payload)


def transform_metadata(name, metadata):
    if name != 'quality':
        raise ValueError(f'Unknown metadata requires review: {name}')
    result = dict(metadata)
    for key in ('counts', 'rates'):
        if key in result:
            result[key] = json.dumps({SIDES.get(k, k): v
                                      for k, v in json.loads(result[key]).items()})
    # Free-text source_error is historical evidence, not a structured arm field.
    return result


def rewrite(source, dest):
    with source.open('rb') as f, dest.open('xb') as output:
        r = make_reader(f, validate_crcs=True)
        summary = r.get_summary()
        if summary is None or summary.statistics.attachment_count:
            raise ValueError('Missing summary or unsupported attachments')
        if set(c.topic for c in summary.channels.values()) != TOPICS:
            raise ValueError('Missing/unknown topics')
        header = r.get_header()
        writer = Writer(output, enable_crcs=True, enable_data_crcs=True)
        writer.start(profile=header.profile, library=header.library)
        schemas = {key: writer.register_schema(s.name, s.encoding, s.data)
                   for key, s in summary.schemas.items()}
        channels = {key: writer.register_channel(SIDES.get(c.topic, c.topic),
                    c.message_encoding, schemas.get(c.schema_id, 0), c.metadata)
                    for key, c in summary.channels.items()}
        for _, c, m in r.iter_messages(log_time_order=False):
            _, raw = transform(c.topic, m.data)
            writer.add_message(channels[c.id], m.log_time, raw,
                               publish_time=m.publish_time, sequence=m.sequence)
        for m in r.iter_metadata():
            writer.add_metadata(m.name, transform_metadata(m.name, m.metadata))
        writer.finish()
        output.flush()
        os.fsync(output.fileno())


def verify(source, dest):
    """Compare every message against the defined transformation, with CRC checks."""
    count = 0
    with source.open('rb') as a, dest.open('rb') as b:
        ra, rb = make_reader(a, validate_crcs=True), make_reader(b, validate_crcs=True)
        for old, new in itertools.zip_longest(ra.iter_messages(log_time_order=False),
                                              rb.iter_messages(log_time_order=False)):
            if old is None or new is None:
                raise ValueError('Message count changed')
            sa, ca, ma = old
            sb, cb, mb = new
            expected_topic, expected_raw = transform(ca.topic, ma.data)
            if (cb.topic != expected_topic or mb.data != expected_raw or
                (ma.log_time, ma.publish_time, ma.sequence) !=
                (mb.log_time, mb.publish_time, mb.sequence) or
                (ca.message_encoding, ca.metadata) != (cb.message_encoding, cb.metadata) or
                (sa.name, sa.encoding, sa.data) != (sb.name, sb.encoding, sb.data)):
                raise ValueError(f'Message verification failed at {count}: {ca.topic}')
            count += 1
        expected = [(m.name, transform_metadata(m.name, m.metadata)) for m in ra.iter_metadata()]
        actual = [(m.name, m.metadata) for m in rb.iter_metadata()]
        if expected != actual:
            raise ValueError('Metadata verification failed')
    return count


def inventory(root, since):
    entries, skipped = [], []
    for p in sorted(root.rglob('*.mcap')):
        s = session(p)
        if s['start_time_ns'] < since:
            skipped.append({'path': str(p.relative_to(root)), 'reason': 'before cutoff'})
            continue
        if s.get('arm_side_repair', {}).get('id') == FIX_ID:
            skipped.append({'path': str(p.relative_to(root)), 'reason': 'already repaired'})
            continue
        configs = s.get('arm_configs', {})
        if (s.get('arm_names') == ['left', 'right'] and set(configs) == set(OLD)
                and all(configs[side]['robot']['channel'] == OLD[other][0]
                        and OLD[other][1] in configs[side]['agent']['port']
                        for side, other in [('left', 'right'), ('right', 'left')])
                and {k: v['serial'] for k, v in s['cameras'].items()} == SERIALS):
            skipped.append({'path': str(p.relative_to(root)), 'reason': 'already correct physical mapping'})
            continue
        if '.in_progress' in p.parts:
            raise ValueError(f'Unfinished recording requires review: {p}')
        check_session(s)
        entries.append({'path': str(p.relative_to(root)), 'sha256': sha(p),
                        'size': p.stat().st_size, 'start_time_ns': s['start_time_ns']})
    # Reject recent HDF5 rather than silently leave an unsupported format unrepaired.
    import h5py
    for p in root.rglob('*.hdf5'):
        with h5py.File(p, 'r') as f:
            created = f.attrs.get('created_at_utc')
            if not created or datetime.fromisoformat(str(created)).timestamp() * 1e9 >= since:
                raise ValueError(f'Recent/undated HDF5 requires separate repair: {p}')
    return {'id': FIX_ID, 'root': str(root), 'since_ns': since, 'entries': entries, 'skipped': skipped}


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def apply(plan, backup):
    root = Path(plan['root'])
    if backup == root or root in backup.parents:
        raise ValueError('Backup must be outside the scanned data tree')
    # No replacements until every planned source fingerprint has passed.
    for entry in plan['entries']:
        p = root / entry['path']
        if sha(p) != entry['sha256']:
            raise ValueError(f'Source changed since inventory: {p}')
    backup.mkdir(parents=True, exist_ok=False)
    (backup / 'plan.json').write_text(json.dumps(plan, indent=2))
    with (backup / 'completed.jsonl').open('x') as log:
        for entry in plan['entries']:
            source = root / entry['path']
            original = backup / entry['path']
            original.parent.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix='.arm-side-repair-', dir=source.parent)
            os.close(fd)
            temp = Path(name)
            temp.unlink()  # rewrite uses exclusive creation
            try:
                rewrite(source, temp)
                count = verify(source, temp)
                if sha(source) != entry['sha256']:
                    raise ValueError(f'Source changed during conversion: {source}')
                shutil.copystat(source, temp)
                os.link(source, original)  # Same disk: retain original inode without copying video.
                sync_dir(original.parent)
                fixed_sha = sha(temp)
                os.replace(temp, source)
                sync_dir(source.parent)
                result = dict(entry, corrected_sha256=fixed_sha, verified_messages=count)
                log.write(json.dumps(result) + '\n')
                log.flush()
                os.fsync(log.fileno())
                print(f"Repaired {entry['path']} ({count} messages verified)", flush=True)
            finally:
                temp.unlink(missing_ok=True)


def repair_configs(directory, backup):
    paths = {side: directory / f'yam_{side}.yaml' for side in OLD}
    originals = {side: path.read_bytes() for side, path in paths.items()}
    check_mapping({side: yaml.safe_load(raw) for side, raw in originals.items()})
    backup.mkdir(parents=True, exist_ok=False)
    for side, raw in originals.items():
        (backup / paths[side].name).write_bytes(raw)
    candidates = {}
    try:
        for side, other in [('left', 'right'), ('right', 'left')]:
            fd, name = tempfile.mkstemp(prefix='.arm-side-config-', dir=directory)
            with os.fdopen(fd, 'wb') as f:
                f.write(b'# Physical side matches wrist camera; whole leader/follower pair relabelled.\n')
                f.write(originals[other])
                f.flush()
                os.fsync(f.fileno())
            candidates[side] = Path(name)
            shutil.copymode(paths[side], name)
        for side, path in paths.items():
            if path.read_bytes() != originals[side]:
                raise ValueError('Configuration changed during preparation')
        for side, path in paths.items():
            os.replace(candidates[side], path)
        sync_dir(directory)
    finally:
        for path in candidates.values():
            path.unlink(missing_ok=True)
    print('Relabelled complete leader/follower pairs; hardware was not opened.')
    print('Existing safety daemon must be safely stopped and restarted before collection.')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, default=Path('data'))
    p.add_argument('--since', default='2026-09-13', help='Inclusive date, Asia/Shanghai')
    p.add_argument('--plan', type=Path)
    p.add_argument('--config-dir', type=Path, help='Repair this rig arm configs instead of data')
    p.add_argument('--apply', action='store_true')
    p.add_argument('--backup-root', type=Path)
    args = p.parse_args()
    if args.config_dir:
        if not args.backup_root or not args.apply:
            p.error('--config-dir requires --apply and --backup-root')
        repair_configs(args.config_dir.resolve(), args.backup_root.resolve())
        return
    if not args.plan:
        p.error('--plan is required for data inventory/repair')
    if args.apply:
        if not args.backup_root:
            p.error('--apply requires --backup-root')
        plan = json.loads(args.plan.read_text())
        if plan['id'] != FIX_ID:
            raise ValueError('Wrong migration plan')
        apply(plan, args.backup_root.resolve())
    else:
        since = int(datetime.fromisoformat(args.since).replace(tzinfo=ZoneInfo('Asia/Shanghai')).timestamp() * 1e9)
        plan = inventory(args.root.resolve(), since)
        args.plan.write_text(json.dumps(plan, indent=2))
        print(f"{len(plan['entries'])} targets; {sum(e['size'] for e in plan['entries']) / 1e9:.3f} GB; {len(plan['skipped'])} skipped")


if __name__ == '__main__':
    main()
