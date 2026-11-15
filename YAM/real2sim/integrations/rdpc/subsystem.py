#!/usr/bin/env python3
"""RDPC TEL placement test adapter; no simulator, hardware or network calls."""
import argparse
from contextlib import contextmanager
from io import BytesIO
import os
from pathlib import Path
import signal
import sqlite3
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import UUID

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(os.environ['RDPC_SOURCE']) / 'rdpc/worker'))
from exchange import publish, validate

TASK_TYPE = 'tel.placement_strategy'
TASK_TYPES = (TASK_TYPE, 'tel.simulation_asset', 'tel.placement_supplement')


@contextmanager
def connection(path):
    # mode=rw: only Worker may create the database/schema.
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=rw', uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    try:
        with db:
            yield db
    finally:
        db.close()


def live(db, attempt, stop):
    row = db.execute('SELECT state,lease_until,deadline FROM rdpc_jobs WHERE attempt_id=?', (attempt,)).fetchone()
    return bool(row and not stop.is_set() and row['state'] == 'running'
                and time.time() < min(row['lease_until'], row['deadline']))


def progress(database, attempt, fraction, stop):
    with connection(database) as db:
        if stop.is_set():
            return False
        now = time.time()
        return db.execute("UPDATE rdpc_tasks SET progress=? WHERE attempt_id=? AND state='running' AND lease_until>? AND deadline>?",
                          (fraction, attempt, now, now)).rowcount == 1


def picture(index):
    image = Image.new('RGB', (640, 360), '#1e2531')
    draw = ImageDraw.Draw(image)
    draw.text((24, 24), 'MOCK / TRANSPORT TEST ONLY', fill='white', font_size=24)
    draw.text((24, 65), 'No simulation. Not an approved distribution.', fill='#ffcd77', font_size=18)
    draw.rectangle((80 + index % 7 * 30, 160, 160 + index % 7 * 30, 240), fill='#45b3ae')
    draw.text((24, 310), f'Synthetic strategy {index + 1}', fill='white', font_size=20)
    stream = BytesIO()
    image.save(stream, format='PNG')
    return stream.getvalue()


def input_metadata(row):
    doc = validate(Path(row['input_path']).parent, row['task_id'], row['task_type'], 'input')
    meta = doc['metadata']
    if meta.get('test_force_failure') is True:
        raise ValueError('test_failure_requested')
    if row['task_type'] == 'tel.simulation_asset':
        for key in ('material_uuid', 'material_code', 'material_name', 'size_text'):
            if not isinstance(meta.get(key), str) or not meta[key].strip():
                raise ValueError('invalid_material_metadata')
        UUID(meta['material_uuid'])
        if not 1 <= len(doc['files']) <= 6:
            raise ValueError('asset_requires_photos')
        for entry in doc['files']:
            if not entry['path'].startswith('files/images/') or not 0 < entry['size_bytes'] <= 2 * 1024 * 1024:
                raise ValueError('invalid_material_photo')
            with Image.open(Path(row['input_path']).parent / entry['path']) as image:
                if image.format not in ('PNG', 'JPEG') or image.width * image.height > 40000000:
                    raise ValueError('invalid_material_image')
                image.verify()
        return meta
    count = meta.get('requested_count')
    if (doc['files'] or type(count) is not int or not 1 <= count <= 200
            or not isinstance(meta.get('description_cn'), str) or not meta['description_cn'].strip()
            or not isinstance(meta.get('materials'), list) or not meta['materials']
            or not all(isinstance(m, dict) for m in meta['materials'])):
        raise ValueError('invalid_placement_input')
    if row['task_type'] == 'tel.placement_supplement' and (type(meta.get('supplement_count')) is not int or meta['supplement_count'] != count):
        raise ValueError('invalid_supplement_count')
    for key in ('strategy_group_id', 'script_task_uuid'):
        if not isinstance(meta.get(key), str):
            raise ValueError('invalid_identity')
        UUID(meta[key])
    if meta.get('test_force_failure') is True:
        raise ValueError('test_failure_requested')
    return meta


def execute_one(database, stop, delay=5):
    if stop.is_set():
        return False
    with connection(database) as db:
        db.execute('BEGIN IMMEDIATE')
        now = time.time()
        row = db.execute("SELECT * FROM rdpc_tasks WHERE task_type IN (?,?,?) AND state='received' AND lease_until>? AND deadline>? ORDER BY rowid LIMIT 1",
                         (*TASK_TYPES, now, now)).fetchone()
        if row is None:
            return False
        changed = db.execute("UPDATE rdpc_tasks SET state='running',progress=.01 WHERE attempt_id=? AND state='received' AND lease_until>? AND deadline>?",
                             (row['attempt_id'], now, now)).rowcount
        if changed != 1:
            return False
        db.execute("INSERT INTO rdpc_logs(attempt_id,message) VALUES(?,'real2sim MOCK: claimed; no simulation or hardware')", (row['attempt_id'],))
    attempt = row['attempt_id']
    end = time.monotonic() + delay
    while time.monotonic() < end:
        if stop.wait(min(.5, max(0, end-time.monotonic()))) or not progress(database, attempt, .05, stop):
            return True
    envelope = dict(schema_version=3, job_id=row['task_id'], attempt_id=attempt,
                    task_type=row['task_type'], direction='output', status='succeeded', metadata=dict(mock=True))
    try:
        meta = input_metadata(row)
        if row['task_type'] == 'tel.simulation_asset':
            if not progress(database, attempt, .8, stop):
                return True
            objects = [('files/assets/mock-placeholder.obj',
                        b'# MOCK transport test; not reconstructed, not for simulation.\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n')]
            envelope['metadata'].update(schema_version=3, attempt_id=attempt,
                material_uuid=meta['material_uuid'], asset_version='real2sim-mock-v1',
                properties=dict(source_size_text=meta['size_text'],
                                note='MOCK placeholder; no real dimensions or reconstruction.'))
        else:
            count = meta['requested_count']
            objects = []
            for index in range(count):
                if not progress(database, attempt, .1 + .8 * index / count, stop):
                    return True
                objects.append((f'files/images/strategy-{index+1:04d}.png', picture(index)))
            envelope['metadata'].update(strategy_group_id=meta['strategy_group_id'], script_task_uuid=meta['script_task_uuid'],
                                        strategy_count=count,
                                        production_mode='human_mock' if row['task_type'] == 'tel.placement_supplement' else 'algorithm_mock',
                                        note='Synthetic transport test; no simulation, human work or confirmed distribution.')
        success, error = True, ''
    except (ValueError, KeyError, TypeError, OSError):
        success, error, objects = False, 'real2sim_mock_input_invalid', []
        envelope.update(status='failed', error=dict(code=error, message='Test input invalid or failure deliberately requested'))
    # All expensive rendering happens outside DB locks. Publish only while lease is valid.
    with connection(database) as db:
        if not live(db, attempt, stop):
            return True
    root = Path(row['output_path'])
    # Bridge prepares an empty files/ directory before handing off the job.
    if root.exists() and any(p.is_symlink() or p.relative_to(root).as_posix() != 'files' or not p.is_dir()
                             for p in root.rglob('*')):
        raise RuntimeError('output_directory_not_empty')
    try:
        publish(root, envelope, objects, success=success)
    except OSError:
        success, error = False, 'real2sim_mock_output_failed'
    with connection(database) as db:
        if not live(db, attempt, stop):
            # Local uncommitted result is never reported; the new job has a different path.
            return True
        now = time.time()
        updated = db.execute("UPDATE rdpc_tasks SET state=?,progress=?,result_uri=?,error_code=? WHERE attempt_id=? AND state='running' AND lease_until>? AND deadline>?",
                             ('succeeded' if success else 'failed', 1 if success else 0,
                              row['output_uri']+'result.json' if success else '', error, attempt, now, now)).rowcount
        if updated == 1:
            db.execute('INSERT INTO rdpc_logs(attempt_id,level,message) VALUES(?,?,?)',
                       (attempt, 'info' if success else 'error', 'real2sim MOCK: local output ready; bridge acknowledgement still required' if success else 'real2sim MOCK: failed'))
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', required=True)
    parser.add_argument('--port', type=int, default=8852)
    args = parser.parse_args()
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())

    class Health(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(200 if self.path == '/health' and not stop.is_set() else 503)
            self.end_headers()

    server = ThreadingHTTPServer(('127.0.0.1', args.port), Health)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        while not stop.is_set():
            if Path(args.database).exists():
                execute_one(args.database, stop)
            stop.wait(.5)
    finally:
        stop.set()
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()
