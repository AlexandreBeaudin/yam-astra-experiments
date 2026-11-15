"""Incremental station evidence and an offline, hardware-free replay exporter."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path
import threading
import time


class EvidenceJournal:
    """Write images before their event; sync each event before returning to motion."""
    def __init__(self, root):
        self.root = Path(root)
        (self.root / 'replay-frames').mkdir(exist_ok=True)
        self.lock = threading.RLock()

    def append(self, event):
        with self.lock:
            row = dict(event)
            row.setdefault('recorded_time_ns', time.time_ns())
            row.setdefault('recorded_monotonic_ns', time.monotonic_ns())
            with (self.root / 'station-events.jsonl').open('a') as stream:
                stream.write(json.dumps(row) + '\n')
                stream.flush()
                os.fsync(stream.fileno())

    def frame(self, view, raw, wall, mono, *, source, acquisition_time_ns=None, frame_id=None):
        digest = hashlib.sha256(raw).hexdigest()
        relative = f'replay-frames/{digest}.jpg'
        path = self.root / relative
        # Content addressing deduplicates unchanged preview pixels.
        with self.lock:
            if not path.exists():
                with path.open('wb') as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
            self.append(dict(kind='camera_frame', camera=view, image=relative,
                sha256=digest, received_time_ns=wall, received_monotonic_ns=mono,
                acquisition_time_ns=acquisition_time_ns, frame_id=frame_id, source=source))
        return relative


class PreviewRecorder:
    """Poll existing preview only, including during model waits; never open cameras."""
    def __init__(self, preview, journal, hz=5):
        self.preview, self.journal, self.hz = preview, journal, hz
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, name='replay-preview', daemon=True)
        self.error = None

    def start(self):
        # Verify recording synchronously before entering a motion session.
        self.capture()
        self.thread.start()

    def capture(self):
        for view in ('main', 'left', 'right'):
            frame = self.preview.frame(view, 1.)
            raw, wall, mono = frame[:3]
            metadata = frame[3] if len(frame) > 3 else {}
            self.journal.frame(view, raw, wall, mono, source='preview_recording',
                acquisition_time_ns=metadata.get('acquisition_time_ns'), frame_id=metadata.get('frame_id'))

    def _run(self):
        while not self.stop_event.wait(1 / self.hz):
            try:
                self.capture()
            except Exception as exc:
                self.error = exc
                try:
                    self.journal.append(dict(kind='recording_error', error=type(exc).__name__))
                finally:
                    return

    def check(self):
        if self.error is not None:
            raise RuntimeError('replay recording failed; stopping episode') from self.error

    def close(self):
        self.stop_event.set()
        if self.thread.ident is not None:
            self.thread.join(timeout=4.)
        if self.thread.is_alive():
            raise RuntimeError('preview recorder failed to stop')


def read_rows(path):
    """Recover complete rows after a hard stop; report any damaged lines."""
    rows, damaged = [], 0
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                damaged += 1
    return rows, damaged


def export_replay(root, *, title=None, harness_label="Inspect Robots"):
    root = Path(root).resolve()
    events, damaged = read_rows(root / 'station-events.jsonl')
    legacy = None
    if not events and (root / 'replay.html').exists():
        text = (root / 'replay.html').read_text()
        legacy = json.JSONDecoder().raw_decode(text.split('const data=', 1)[1].lstrip())[0]
        events = legacy.get('events', [])
    frames = [e for e in events if e.get('kind') == 'camera_frame']
    frames.sort(key=lambda e: e['received_monotonic_ns'])
    start = min((e['received_monotonic_ns'] for e in frames), default=0)
    missing = 0
    for e in frames:
        e['time_s'] = (e['received_monotonic_ns'] - start) / 1e9
        if not e.get('image'):
            missing += 1
            continue
        path = (root / e['image']).resolve()
        if root not in path.parents:
            raise ValueError('frame path escapes run directory')
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != e['sha256']:
            e['image'] = None
            missing += 1
        e['time_s'] = (e['received_monotonic_ns'] - start) / 1e9
    for e in events:
        e.setdefault('time_s', (e.get('recorded_monotonic_ns', start) - start) / 1e9)
    calls = []
    metadata, returns = {}, {}
    for path in sorted((root / 'records').rglob('record.json')):
        record = json.loads(path.read_text())
        config = record.get('configuration', {}).get('policy', {}).get('config', {})
        metadata.update({k: config[k] for k in ('model', 'effort') if k in config})
    framework = root / 'framework-log.json'
    if framework.exists():
        samples = json.loads(framework.read_text()).get('samples', [])
        if samples:
            metadata['goal'] = samples[0].get('instruction')
    for path in sorted((root / 'transcripts').rglob('*.jsonl')):
        rows, _ = read_rows(path)
        returns.update({r['tool_call_id']: r.get('content', '') for r in rows
                        if r.get('role') == 'tool' and r.get('tool_call_id')})
    for path in sorted((root / 'wire').rglob('calls.jsonl')):
        rows, bad = read_rows(path)
        damaged += bad
        for row in rows:
            request = row.get('request') or {}
            if request.get('model'):
                metadata['model'] = request['model']
            if request.get('reasoning', {}).get('effort'):
                metadata['effort'] = request['reasoning']['effort']
        # Return content is reviewable; never embed credentials or request headers.
        calls.extend({k: row.get(k) for k in ('call', 't', 'duration_s', 'status', 'response', 'error')} for row in rows)
    result_path = root / 'attempt-result.json'
    result = json.loads(result_path.read_text()) if result_path.exists() else {'evaluation_completed': False, 'status': 'partial / no final result'}
    payload = dict(frames=frames, events=events, calls=calls, result=result,
                   damaged_lines=damaged, missing_frames=missing)
    if legacy is not None:
        payload = legacy
    from replay_timeline import build_timeline
    payload['presentation'] = build_timeline(payload, title=title, harness_label=harness_label, metadata=metadata, returns=returns)
    assets = Path(__file__).with_name('replay_assets')
    shutil.copytree(assets, root / 'replay_assets', dirs_exist_ok=True)
    template = Path(__file__).with_name('hardware_replay.html').read_text()
    target = root / 'replay.html'
    temporary = root / 'replay.html.tmp'
    temporary.write_text(template.replace('__DATA__', json.dumps(payload).replace('<', '\\u003c')))
    temporary.replace(target)
    return target


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    args = parser.parse_args()
    print(export_replay(args.run_dir))
