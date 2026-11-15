"""Durable metadata-only outbox. Network work never runs in the motor/record loop."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
import uuid


def outbox_path():
    return Path.home() / '.local/state/yam-operator/rdp-progress.sqlite3'


class Outbox:
    def __init__(self, path=None):
        self.path = Path(path or outbox_path())
        self.retry_after = {}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        with self.db() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY, id TEXT UNIQUE, batch TEXT, payload TEXT, target TEXT, state TEXT, error TEXT DEFAULT "", inode INTEGER DEFAULT 0, device INTEGER DEFAULT 0)')
            db.execute('CREATE INDEX IF NOT EXISTS events_batch ON events(batch,state)')
            db.execute("CREATE INDEX IF NOT EXISTS events_episode ON events(batch,json_extract(payload,'$.event.episode_id'))")

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.execute('PRAGMA synchronous=FULL')
        try:
            with db:
                yield db
        finally:
            db.close()

    def add(self, payload, target='', source=None):
        st = Path(source).stat() if source else None
        with self.db() as db:
            old = db.execute('SELECT payload FROM events WHERE id=?', (payload['event']['event_id'],)).fetchone()
            if old and json.loads(old[0])['event']['outcome'] != payload['event']['outcome']:
                raise ValueError('请重试原保存结果，或先废弃未发布记录')
            db.execute('INSERT OR IGNORE INTO events(id,batch,payload,target,state,inode,device) VALUES(?,?,?,?,?,?,?)',
                       (payload['event']['event_id'], payload['batch_uuid'], json.dumps(payload), str(target),
                        'publishing' if target else 'pending', st.st_ino if st else 0, st.st_dev if st else 0))

    def recover(self):
        # A commit intent only becomes reportable after the exact source inode is published.
        with self.db() as db:
            for seq, target, inode, device in db.execute('SELECT seq,target,inode,device FROM events WHERE state="publishing"').fetchall():
                try:
                    st = Path(target).stat()
                    if st.st_ino != inode or st.st_dev != device:
                        db.execute('UPDATE events SET error=? WHERE seq=?', ('文件身份不匹配，请联系管理员', seq))
                        continue
                    fd = os.open(target, os.O_RDONLY)
                    try:
                        os.fsync(fd)
                    finally:
                        os.close(fd)
                    fd = os.open(str(Path(target).parent), os.O_RDONLY)
                    try:
                        os.fsync(fd)
                    finally:
                        os.close(fd)
                    db.execute('UPDATE events SET state="pending",error="" WHERE seq=?', (seq,))
                except FileNotFoundError:
                    pass

    def completed_ordinals(self, batch):
        # Count locally published successes even while the platform is offline.
        import base64
        result = set()
        with self.db() as db:
            rows = db.execute("SELECT payload,target,state,inode,device FROM events WHERE batch=? AND json_extract(payload,'$.event.kind')='saved' AND json_extract(payload,'$.event.outcome')='success'", (batch,)).fetchall()
        for raw, target, state, inode, device in rows:
            if state == 'publishing':
                try:
                    st = Path(target).stat()
                    if st.st_ino != inode or st.st_dev != device:
                        continue
                except FileNotFoundError:
                    continue
            payload = json.loads(raw)['grant'].split('.')[1]
            claims = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
            result.add(claims['ordinal'])
        return sorted(result)

    def summary(self, batch):
        with self.db() as db:
            rows = db.execute('SELECT state,count(*) FROM events WHERE batch=? GROUP BY state', (batch,)).fetchall()
            error = db.execute('SELECT error FROM events WHERE batch=? AND error<>"" ORDER BY seq DESC LIMIT 1', (batch,)).fetchone()
            unfinished = db.execute("SELECT count(*) FROM events a WHERE a.batch=? AND json_extract(a.payload,'$.event.kind')='started' AND NOT EXISTS (SELECT 1 FROM events b WHERE b.batch=a.batch AND json_extract(b.payload,'$.event.episode_id')=json_extract(a.payload,'$.event.episode_id') AND json_extract(b.payload,'$.event.kind') IN ('saved','discarded','stopped') AND b.state<>'publishing')", (batch,)).fetchone()[0]
        counts = dict(rows)
        return {'pending': sum(v for k, v in rows if k != 'synced'), 'synced': counts.get('synced', 0),
                'unfinished': unfinished, 'blocked': counts.get('blocked', 0), 'message': error[0] if error else ''}

    def flush(self, send):
        self.recover()
        with self.db() as db:
            # Preserve each task's ordering; rejected tasks do not block other tasks.
            rows = db.execute('SELECT e.seq,e.payload,e.state FROM events e WHERE e.state<>"synced" AND NOT EXISTS (SELECT 1 FROM events prior WHERE prior.batch=e.batch AND prior.seq<e.seq AND prior.state<>"synced") ORDER BY e.seq LIMIT 100').fetchall()
            row = next((r for r in rows if r[2] != 'publishing' and self.retry_after.get(r[0], 0) <= time.monotonic()), None)
        if not row or row[2] == 'publishing':
            return False
        seq, payload, _ = row
        try:
            send(json.loads(payload))
        except Exception as e:
            blocked = isinstance(e, urllib.error.HTTPError) and 400 <= e.code < 500 and e.code != 429
            # Do not persist tokens, response bodies, URLs, or arbitrary transport exception text.
            message = ('平台拒绝同步（权限、任务状态或事件冲突），本地数据保留，请联系管理员'
                       if blocked else '平台暂不可达，本地数据已保留，将自动重试')
            with self.db() as db:
                db.execute('UPDATE events SET state=?,error=? WHERE seq=?', ('blocked' if blocked else 'pending', message, seq))
            self.retry_after[seq] = time.monotonic() + 30
            return False
        self.retry_after.pop(seq, None)
        with self.db() as db:
            db.execute('UPDATE events SET state="synced",error="" WHERE seq=?', (seq,))
        return True


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Reporter:
    def __init__(self, config, outbox=None):
        self.config = config
        url = urlsplit(config['report_url'])
        if url.scheme != 'https' or not url.hostname or url.username or url.query or url.fragment:
            raise ValueError('report_url must be a trusted HTTPS endpoint')
        self.token = Path(config['report_token_file']).read_text().strip()
        if len(self.token) < 32:
            raise ValueError('report token must contain at least 32 characters')
        self.outbox = outbox or Outbox()
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def send(self, payload):
        body = {k: payload[k] for k in ('device_id', 'grant', 'event')}
        req = urllib.request.Request(self.config['report_url'], json.dumps(body).encode(),
                                     {'Content-Type': 'application/json', 'X-RDP-Device-Token': self.token})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(req, timeout=10) as response:
            ack = json.loads(response.read(4096))
        if ack.get('acknowledged') != body['event']['event_id']:
            raise ValueError('Invalid receipt acknowledgement')

    def run(self):
        delay = 1
        while not self.stopped.is_set():
            try:
                sent = self.outbox.flush(self.send)
                delay = 0.1 if sent else min(max(delay * 2, 1), 30)
            except Exception:
                delay = 10
            self.stopped.wait(delay)


class EpisodeProgress:
    """Called at episode boundaries only; immutable grant and strategy per episode."""
    def __init__(self, context, outbox=None):
        self.context = Path(context)
        self.outbox = outbox or Outbox()
        self.identity = None
        self.started = None
        self.episode = None
        self.terminal = None

    def start(self):
        identity = json.loads(self.context.read_text())
        if not identity.get('_report_grant'):
            raise ValueError('RDP reporting grant missing; reconnect the workbench')
        now = int(time.time() * 1000)
        if now >= identity['_grant_expires'] * 1000:
            raise ValueError('平台授权已过期，请重新授权后开始采集')
        self.identity, self.started = identity, now
        self.episode, self.terminal = str(uuid.uuid4()), None
        self.outbox.add(self.payload('started'))

    def payload(self, kind, outcome='', duration_ms=0, frames=0):
        c = self.identity
        return {'batch_uuid': c['batch_uuid'], 'device_id': c['aud'], 'grant': c['_report_grant'],
                'event': {'event_id': str(uuid.uuid5(uuid.UUID(self.episode), kind)), 'episode_id': self.episode,
                          'kind': kind, 'outcome': outcome, 'started_at': self.started,
                          'occurred_at': int(time.time() * 1000), 'duration_ms': duration_ms, 'frames': frames}}

    def before_commit(self, source, target, outcome, duration_ms, frames):
        if self.terminal and self.terminal != 'saved':
            raise ValueError('RDP episode already discarded')
        self.outbox.add(self.payload('saved', outcome, duration_ms, frames), target, source)
        self.terminal = 'saved'

    def cancel_unpublished(self):
        if self.terminal != 'saved':
            return
        event_id = str(uuid.uuid5(uuid.UUID(self.episode), 'saved'))
        with self.outbox.db() as db:
            row = db.execute('SELECT target,state FROM events WHERE id=?', (event_id,)).fetchone()
            if row and (Path(row[0]).exists() or row[1] != 'publishing'):
                raise ValueError('文件已发布，请重试原保存结果，不能废弃')
            db.execute('DELETE FROM events WHERE id=?', (event_id,))
        self.terminal = None

    def discard(self):
        if self.identity and not self.terminal:
            self.outbox.add(self.payload('discarded'))
            self.terminal = 'discarded'


def clear_incomplete(outbox, data_root, batch):
    """Caller must hold the device/app locks and confirm stopped + disarmed.

    Quarantine, never delete media. Filesystem moves may precede the DB commit;
    a retry closes the remaining orphan events even if all files already moved.
    """
    batch = str(uuid.UUID(batch))
    root = Path(data_root).expanduser() / ('RDP-' + batch) / 'mcap'
    source = root / '.in_progress'
    archive = root / '.discarded'
    if root.parent.is_symlink() or root.is_symlink() or source.is_symlink() or archive.is_symlink():
        raise ValueError('记录目录不能是符号链接')
    outbox.recover()  # Honor any commit already published before the process stopped.
    files = sorted(source.glob('*.mcap'))
    if len(files) > 1000:
        raise ValueError('未完成记录过多，请联系管理员')
    with outbox.db() as db:
        db.execute('BEGIN IMMEDIATE')
        # A failed publication intent is not a saved result. Never erase a published one.
        intents = db.execute('SELECT seq,target FROM events WHERE batch=? AND state="publishing"', (batch,)).fetchall()
        for _, target in intents:
            if Path(target).exists():
                raise ValueError('存在已发布但尚未核对的记录，请先处理同步错误')
        for path in files:
            if path.is_symlink() or not path.is_file():
                raise ValueError('未完成记录类型不正确')
            for outcome in ('success', 'fail'):
                target = root / outcome / path.name
                if target.exists() and not os.path.samefile(path, target):
                    raise ValueError('记录名称与已保存文件冲突，请联系管理员')
        if files:
            archive.mkdir(parents=True, exist_ok=True)
            from gello.data_utils.mcap_recording import sync_directory
            sync_directory(root)
            for path in files:
                # Already published hard links only need temporary-name cleanup.
                if any((root / label / path.name).exists() for label in ('success', 'fail')):
                    path.unlink()
                    continue
                destination = archive / path.name
                if destination.exists():
                    raise ValueError('隔离目录已有同名文件，请联系管理员')
                os.rename(path, destination)
            sync_directory(archive)
            sync_directory(source)
        for seq, _ in intents:
            db.execute('DELETE FROM events WHERE seq=?', (seq,))
        starts = db.execute("SELECT a.payload FROM events a WHERE a.batch=? AND json_extract(a.payload,'$.event.kind')='started' AND NOT EXISTS (SELECT 1 FROM events b WHERE b.batch=a.batch AND json_extract(b.payload,'$.event.episode_id')=json_extract(a.payload,'$.event.episode_id') AND json_extract(b.payload,'$.event.kind') IN ('saved','discarded','stopped'))", (batch,)).fetchall()
        for (raw,) in starts:
            payload = json.loads(raw)
            event = payload['event']
            event.update(event_id=str(uuid.uuid5(uuid.UUID(event['episode_id']), 'discarded')),
                         kind='discarded', outcome='', occurred_at=int(time.time()*1000), duration_ms=0, frames=0)
            db.execute('INSERT OR IGNORE INTO events(id,batch,payload,target,state) VALUES(?,?,?,"","pending")',
                       (event['event_id'], batch, json.dumps(payload)))
    return {'quarantined': len(files), 'closed_episodes': len(starts), 'archive': str(archive)}
