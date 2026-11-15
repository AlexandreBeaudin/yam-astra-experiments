"""Optional local-only camera/episode bridge. No robot or camera device ownership.

Control callbacks only update memory or enqueue events. JPEG encoding, filesystem
operations and ticket polling run in one daemon worker. Compatible with Python 3.8.
"""
import functools
import hashlib
import atexit
import copy
import json
import os
import queue
import threading
import time
import uuid
from pathlib import Path


def atomic_json(path, value):
    path = Path(path)
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    os.replace(str(tmp), str(path))


def synchronized(method):
    @functools.wraps(method)
    def wrapped(self, *args, **kwargs):
        with self.lock:
            return method(self, *args, **kwargs)
    return wrapped


class GuidanceBridge:
    def __init__(self, directory, task_id, camera_ids, camera_hash):
        import fcntl
        self.directory = Path(directory).expanduser().resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.owner = (self.directory / 'producer.lock').open('a')
        try:
            fcntl.flock(self.owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.owner.close()
            raise ValueError('另一个数采进程正在使用 guidance-dir')
        self.session = uuid.uuid4().hex
        self.task_id = task_id
        self.camera_hash = camera_hash
        self.camera_ids = {str(k): str(v) for k, v in camera_ids.items()}
        self.lock = threading.RLock()
        self.last_completed = None
        self.latest = {}
        self.selection = None
        self.projection = None
        self.projection_cache = {}
        self.episode = None
        self.record = None
        self.events = queue.Queue()
        self.stopping = threading.Event()
        self.worker = threading.Thread(target=self._run, name='guidance-io', daemon=True)
        self.worker.start()
        atexit.register(self.close)

    def offer(self, name, image):
        # Camera thread transfers the freshly read immutable frame reference.
        with self.lock:
            self.latest[name] = (image, time.time())

    @synchronized
    def begin(self, episode):
        """Freeze the currently loaded guidance at the recording-button transition."""
        episode = str(episode)
        if self.episode == episode:
            return
        if self.episode is not None:
            self.finish('episode_changed')
        with self.lock:
            selection = copy.deepcopy(self.selection)
            projection = self.projection
        now = time.time()
        valid = (isinstance(selection, dict) and projection is not None
                 and selection.get('session_id') == self.session
                 and selection.get('task_id') == self.task_id
                 and selection.get('camera_hash') == self.camera_hash)
        if valid:
            self.projection_cache[selection['revision']] = projection
        self.record = dict(schema_version=2, session_id=self.session, task_id=self.task_id,
                           episode_path=episode, started_at=now, status='recording',
                           association_status='guidance_saved' if valid else 'unlinked',
                           reason=None if valid else 'no_loaded_guidance_for_this_session',
                           guidance=selection if valid else None, frame_count=0,
                           episode_id=uuid.uuid4().hex,
                           camera_hash=self.camera_hash, camera_ids=self.camera_ids,
                           placement_validation='pending_first_frame_review',
                           quality_control='not_reviewed')
        self.episode = episode

    @synchronized
    def saved_frame(self, episode, index):
        if self.episode != str(episode):
            self.begin(episode)
        self.record['frame_count'] += 1
        self.record['last_frame_index'] = int(index)
        if self.record['frame_count'] == 1:
            self.record['first_frame_index'] = int(index)
            self.record['first_frame_path'] = 'topImg/{}.jpg'.format(index)
        if self.record['frame_count'] == 1 or self.record['frame_count'] % 25 == 0:
            self.events.put_nowait(copy.deepcopy(self.record))

    @synchronized
    def finish(self, reason='recording_stopped'):
        if self.record is not None and self.record['frame_count']:
            self.record.update(status='recording_stopped', stopped_at=time.time(), stop_reason=reason)
            self.events.put_nowait(copy.deepcopy(self.record))
        self.episode = self.record = None

    def _persist(self, record):
        path = Path(record['episode_path'])
        path.mkdir(parents=True, exist_ok=True)
        if record['guidance'] is not None:
            import cv2
            import numpy as np
            raw = self.projection_cache[record['guidance']['revision']]
            target = path / 'guidance_projection.png'
            if not target.exists():
                tmp = path / 'guidance_projection.tmp'
                tmp.write_bytes(raw)
                os.replace(str(tmp), str(target))
            first = path / record['first_frame_path']
            if not first.is_file():
                raise OSError('First recorded top image is not available: {}'.format(first))
            record['first_frame_sha256'] = hashlib.sha256(first.read_bytes()).hexdigest()
            record['projection_file'] = 'guidance_projection.png'
            record['projection_sha256'] = hashlib.sha256(raw).hexdigest()
            overlay_path = path / 'guidance_overlay_first_frame.jpg'
            if not overlay_path.exists():
                rgba = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_UNCHANGED)
                frame = cv2.imread(str(first))
                if frame is None or rgba is None or rgba.shape != (480,640,4) or frame.shape != (480,640,3):
                    raise ValueError('Invalid projection or first frame dimensions')
                alpha = rgba[:,:,3:]/255.0 * record['guidance']['opacity']
                composite = (rgba[:,:,:3]*alpha+frame*(1-alpha)).astype(np.uint8)
                ok, encoded = cv2.imencode('.jpg', composite)
                if not ok:raise ValueError('Cannot encode first-frame overlay')
                tmp = path / 'guidance_overlay.tmp'
                tmp.write_bytes(encoded.tobytes());os.replace(str(tmp),str(overlay_path))
            record['overlay_file'] = 'guidance_overlay_first_frame.jpg'
        atomic_json(path / 'placement_guidance.json', record)
        events = self.directory / 'episodes'
        events.mkdir(exist_ok=True)
        key = self.session + '_' + path.name
        atomic_json(events / (key + '.json'), record)
        atomic_json(self.directory / 'recording.json', record)
        if record['status'] == 'recording_stopped':
            with self.lock:
                self.last_completed = copy.deepcopy(record)
                if (self.episode is None and record.get('quality_control') != 'failed'
                        and record.get('stop_reason') == 'recording_stopped'):
                    self._advance(record)

    def _set_selection(self, selection):
        raw = (self.directory / 'projections' / (selection['projection_sha256']+'.png')).read_bytes()
        if hashlib.sha256(raw).hexdigest() != selection['projection_sha256']:
            raise ValueError('Projection digest mismatch')
        selection = copy.deepcopy(selection)
        selection.update(revision=uuid.uuid4().hex, selected_at=time.time())
        atomic_json(self.directory / 'selected.json', selection)
        self.selection, self.projection = selection, raw

    def _advance(self, record):
        guidance = record.get('guidance')
        if not guidance or not self.selection or self.selection['revision'] != guidance['revision']:
            return
        try:
            current = json.loads((self.directory / 'selected.json').read_text())
            if current.get('revision') != guidance['revision']:
                return
            phase = guidance.get('capture_phase', 'collection')
            plan_path = self.directory / ('workflow_' + phase + '.json')
            if not plan_path.exists():
                plan_path = self.directory / 'workflow.json'
            plan = json.loads(plan_path.read_text())
            if plan['session_id'] != self.session or plan['binding_hash'] != guidance['binding_hash']:
                return
            items = plan['selections']
            index = next(i for i, item in enumerate(items) if item['layout_id'] == guidance['layout_id'])
            if index + 1 < len(items):
                self._set_selection(dict(items[index + 1], opacity=guidance['opacity']))
        except (OSError, ValueError, KeyError, StopIteration):
            return

    def _commands(self):
        requests = self.directory / 'commands'
        requests.mkdir(exist_ok=True)
        for path in sorted(requests.glob('*.request.json')):
            result_path = path.with_name(path.name.replace('.request.json', '.result.json'))
            if result_path.exists():
                continue
            try:
                request = json.loads(path.read_text())
                with self.lock:
                    record = self.last_completed
                    if request.get('session_id') != self.session:
                        raise ValueError('数采会话已变化，请刷新页面')
                    if self.episode is not None or not self.events.empty():
                        raise ValueError('正在录制或保存，请停止并等待保存完成')
                    if not record or request.get('episode_id') != record.get('episode_id'):
                        raise ValueError('只能标记本会话最近保存的一条轨迹')
                    if request.get('action') != 'fail':
                        raise ValueError('Unknown command')
                    if record.get('quality_control') != 'failed':
                        source = Path(record['episode_path'])
                        target = source.parent / 'fail' / source.name
                        if target.exists():
                            raise ValueError('fail 目录中已存在同名轨迹，未覆盖')
                        target.parent.mkdir(exist_ok=True)
                        # Persist the intended destination before the atomic same-disk move.
                        record = copy.deepcopy(record)
                        record.update(quality_control='failed', failed_at=time.time(),
                                      original_episode_path=str(source), episode_path=str(target))
                        atomic_json(source / 'placement_guidance.json', record)
                        source.rename(target)
                        self.last_completed = record
                    self._persist(record)
                    if record.get('guidance'):
                        self._set_selection(record['guidance'])
                    result = dict(ok=True, episode_id=record['episode_id'], episode_path=record['episode_path'])
            except (OSError, ValueError, KeyError) as exc:
                result = dict(ok=False, error=str(exc))
            atomic_json(result_path, result)

    def _tick(self):
        import cv2
        self._commands()
        selection = None
        projection = None
        try:
            candidate = json.loads((self.directory / 'selected.json').read_text())
            if (candidate.get('session_id') == self.session and candidate.get('task_id') == self.task_id
                    and candidate.get('camera_hash') == self.camera_hash):
                sha = candidate['projection_sha256']
                if len(sha) != 64 or any(c not in '0123456789abcdef' for c in sha):
                    raise ValueError('Invalid projection digest')
                raw = (self.directory / 'projections' / (sha+'.png')).read_bytes()
                if hashlib.sha256(raw).hexdigest() != sha:raise ValueError('Projection digest mismatch')
                selection, projection = candidate, raw
        except (OSError, ValueError, KeyError):
            pass
        with self.lock:
            self.selection, self.projection = selection, projection
            latest = dict(self.latest)
        stamps = {}
        for name, (image, captured) in latest.items():
            stamps[name] = captured
            # Do not refresh a stale frame's timestamp when a camera stops.
            if time.time() - captured > 2:
                continue
            ok, encoded = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, 85])
            if ok:
                tmp = self.directory / (name + '.tmp')
                tmp.write_bytes(encoded.tobytes())
                os.replace(str(tmp), str(self.directory / (name + '.jpg')))
        atomic_json(self.directory / 'producer.json', dict(session_id=self.session, task_id=self.task_id,
                    camera_hash=self.camera_hash, camera_ids=self.camera_ids, time=time.time(),
                    active_episode=self.episode, captured_at=stamps, selected_revision=selection['revision'] if selection else None, image_size=[640,480], orientation='collector_saved_image'))

    def _run(self):
        pending = None
        while not self.stopping.is_set() or not self.events.empty() or pending is not None:
            try:
                while pending is not None or not self.events.empty():
                    if pending is None:
                        pending = self.events.get_nowait()
                    self._persist(pending)
                    pending = None
                if not self.stopping.is_set():
                    self._tick()
            except Exception as exc:
                print('[guidance] bridge I/O failed: {}'.format(exc), flush=True)
            self.stopping.wait(.1)

    def close(self):
        if self.stopping.is_set():
            return
        self.finish('collector_exit')
        self.stopping.set()
        self.worker.join(timeout=3)
        self.owner.close()
