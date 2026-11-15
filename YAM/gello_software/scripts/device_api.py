"""Optional HTTPS bridge for RDP; only explicit metadata receipts go to the platform."""
import base64
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import re
import secrets
import ssl
import threading
import time
import urllib.request
from urllib.parse import urlsplit
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from PIL import Image

MAX_IMAGE = 8 * 1024 * 1024
SAFE_ACTIONS = {'stop', 'off', 'finish', 'success', 'fail', 'discard', 'clear_incomplete'}


class DeviceError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code = status, code


def reject(status, code, message):
    raise DeviceError(status, code, message)


def decode64(value):
    return base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_', validate=True)


class DeviceBridge:
    def __init__(self, app, config, clock=time.time):
        self.app, self.config, self.clock = app, config, clock
        self.key = Ed25519PublicKey.from_public_bytes(base64.b64decode(config['public_key'], validate=True))
        self.lock = threading.RLock()
        self.challenges = {}
        self.session = None
        self.reporter = None
        if config.get('report_url'):
            from gello.data_utils.platform_progress import Reporter
            self.reporter = Reporter(config)
            self.reporter.thread.start()
        self.context = Path.home() / '.local/state/yam-operator/platform-context.json'

    def challenge(self):
        with self.lock:
            now = self.clock()
            self.challenges = {k: v for k, v in self.challenges.items() if now-v < 60}
            if len(self.challenges) >= 128:
                reject(429, 'device_busy', '连接请求过多，请稍后重试')
            nonce = secrets.token_urlsafe(32)
            self.challenges[nonce] = now
            return {'nonce': nonce, 'device_id': self.config['device_id'], 'station_id': self.config['station_id']}

    def verify(self, token, origin):
        try:
            prefix, payload, signature = token.split('.')
            if prefix != 'rdp-device-v1' or len(token) > 8192:
                raise ValueError()
            self.key.verify(decode64(signature), (prefix+'.'+payload).encode())
            c = json.loads(decode64(payload))
            now = self.clock()
            if (c['iss'] != 'rhosdata-platform/device-v1' or c['aud'] != self.config['device_id']
                    or c['station_id'] != self.config['station_id'] or c['origin'] != origin
                    or c['iat'] > now+5 or not now < c['exp'] <= c['iat']+120
                    or c['guidance_kind'] != 'reference'
                    or not re.fullmatch(r'[a-f0-9-]{36}', c['batch_uuid'])
                    or not c['sub'] or not c['strategy_id'] or not 1 <= c['ordinal'] <= 200):
                raise ValueError()
            created = self.challenges.get(c['nonce'])
            if created is None or now-created >= 60:
                raise ValueError()
            return c
        except Exception:
            reject(401, 'invalid_device_grant', '授权已过期、已使用或不匹配，请重新连接')

    def owned(self, token, origin, fresh=False):
        s = self.session
        if not s or not isinstance(token, str) or not hmac.compare_digest(token, s['token']) or origin != s['claims']['origin']:
            reject(403, 'device_session_required', '此页面没有设备控制权，请重新连接')
        if fresh and self.clock() >= s['claims']['exp']:
            reject(401, 'device_authorization_expired', '平台授权已过期；可结束并保存本条，请重新授权后开始下一条')
        return s

    def connect(self, body, origin, token=None):
        with self.lock, self.app.lock:
            c = self.verify(body.get('grant', ''), origin)
            old = self.session
            # A lost browser session can only be replaced after expiry and confirmed disarm.
            state = self.app.snapshot()
            if old and self.clock() >= old['claims']['exp'] and not state['running'] and not self.app.busy and state['safety']['state'] in ('offline', 'disarmed') and not self.app.collector_running():
                old = None
            if old:
                self.owned(token, origin)
                if any(old['claims'][k] != c[k] for k in ('sub', 'batch_uuid', 'aud', 'station_id')):
                    reject(409, 'device_owned', '请先结束当前任务并断开设备，再切换人员或任务')
                changed = any(old['claims'][k] != c[k] for k in ('strategy_id', 'ordinal', 'image_sha256'))
                if changed and self.app.snapshot()['running']:
                    control = self.app.collection('claim', client=old['client'], release=False)
                    if control['phase'] != 'ready' or control.get('pending') or not control.get('owns_control'):
                        reject(409, 'device_recording', '请先保存或废弃当前记录，再切换摆放策略')
                if self.app.busy:
                    reject(409, 'device_busy', '设备操作尚未完成，请稍后重试')
            elif self.app.snapshot()['running'] or self.app.busy or self.app.collector_running():
                # Diagnose ownership rejection without logging grants or session tokens.
                diagnostic = self.app.snapshot()
                try:
                    collector_phase = self.app.collection().get('phase')
                except Exception as exc:
                    collector_phase = type(exc).__name__
                print('YAM_DEVICE_CONNECT_BLOCKED ' + json.dumps({
                    'pid': os.getpid(), 'running': diagnostic.get('running'),
                    'busy': diagnostic.get('busy'), 'phase': diagnostic.get('phase'),
                    'safety': diagnostic.get('safety', {}).get('state'),
                    'collector_phase': collector_phase,
                }), flush=True)
                reject(409, 'device_owned', '本机已有任务运行，请先在原页面停止任务')
            try:
                raw = base64.b64decode(body.get('image_base64', ''), validate=True)
                if not raw or len(raw) > MAX_IMAGE or hashlib.sha256(raw).hexdigest() != c['image_sha256']:
                    raise ValueError()
                with Image.open(io.BytesIO(raw)) as im:
                    if im.format not in ('PNG', 'JPEG') or list(im.size) != [c['image_width'], c['image_height']] or max(im.size)>4096:
                        raise ValueError()
                    im.verify()
            except Exception:
                reject(400, 'device_image_mismatch', '图片与平台发布版本不一致，请重新加载')
            s = dict(old) if old else {'token': secrets.token_urlsafe(32), 'client': 'rdp-'+secrets.token_hex(16)}
            s['claims'] = c
            snapshot = {k: c[k] for k in ('sub', 'batch_uuid', 'task_id', 'aud', 'station_id', 'strategy_id', 'ordinal', 'image_sha256', 'image_width', 'image_height', 'guidance_kind')}
            snapshot.update(_report_grant=body['grant'], _grant_expires=c['exp'])
            snapshot.update(source='rdp', calibration_status='unverified_reference', image_base64=base64.b64encode(raw).decode())
            self.context.parent.mkdir(parents=True, exist_ok=True)
            temp = self.context.with_suffix('.tmp')
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'w') as out:
                json.dump(snapshot, out, ensure_ascii=False)
            os.replace(temp, self.context)
            self.challenges.pop(c['nonce'])
            self.session = s
            self.app.device_owner = s['token']
            return {'session': s['token'], 'expires_at': c['exp'], 'guidance_kind': 'reference'}

    def status(self, token, origin):
        with self.lock:
            s = self.owned(token, origin)
            out = self.app.snapshot()
            out['authorization_valid'] = self.clock() < s['claims']['exp']
            out['device_id'] = self.config['device_id']
            out['progress_sync'] = self.reporter.outbox.summary(s['claims']['batch_uuid']) if self.reporter else {'pending': 0, 'synced': 0, 'blocked': 0, 'message': '设备未配置进度回写', 'configured': False}
            out['incomplete_count'] = len(list((Path(self.app.profile.storage.root).expanduser() / ('RDP-' + s['claims']['batch_uuid']) / 'mcap/.in_progress').glob('*.mcap'))) if not out['running'] else 0
            out['completed_ordinals'] = self.reporter.outbox.completed_ordinals(s['claims']['batch_uuid']) if self.reporter else []
            out['collection'] = None
            if out['running'] and out.get('phase') != 'initializing':
                try:
                    out['collection'] = self.app.collection('claim', client=s['client'], release=False)
                    if out['collection'].get('task', out.get('task')) != out.get('task'):
                        out['collection'] = None
                        out['control_error'] = '采集器正在切换任务，请等待就绪'
                except Exception:
                    # No takeover. Stop remains available through the safety path.
                    out['control_error'] = '采集控制连接尚未就绪或被另一页面占用'
            return out

    def action(self, body, token, origin):
        with self.lock:
            name = body.get('action')
            if name not in {'check', 'enable', 'stop', 'off', 'start', 'finish', 'success', 'fail', 'discard', 'clear_incomplete'}:
                reject(400, 'invalid_device_action', '请选择有效操作')
            s = self.owned(token, origin, fresh=name not in SAFE_ACTIONS)
            if name == 'clear_incomplete':
                return self.clear_incomplete(body, s)
            if name in {'check', 'enable', 'stop', 'off'}:
                if name == 'enable' and not self.reporter:
                    reject(503, 'device_reporting_unconfigured', '请先配置设备进度回写再开始新任务')
                if name == 'enable' and not self.app.snapshot()['check_passed']:
                    reject(409, 'device_check_required', '请先完成设备检查')
                if name == 'enable' and body.get('reference_confirmed') is not True:
                    reject(400, 'reference_confirmation_required', '当前为未标定参考图，请确认现场摆放后继续')
                data = dict(action=name, revision=body.get('revision'), confirmed=body.get('confirmed') is True,
                            task='RDP-'+s['claims']['batch_uuid'], guided=False,
                            _device_session=s['token'], _platform_context=str(self.context))
                self.app.action(data)
                return {'accepted': True}
            if name == 'start' and self.reporter:
                if s['claims']['ordinal'] in self.reporter.outbox.completed_ordinals(s['claims']['batch_uuid']):
                    reject(409, 'device_strategy_completed', '本摆放策略已保存成功，请切换下一条策略')
            revision = body.get('collection_revision')
            if type(revision) is not int:
                reject(409, 'device_state_changed', '请等待最新采集状态后重试')
            return self.app.collection(name, client=s['client'], release=False, expected_revision=revision)

    def clear_incomplete(self, body, session):
        with self.app.lock:
            state = self.app.snapshot()
            if body.get('confirmed') is not True:
                reject(400, 'device_cleanup_confirmation', '请确认废弃未保存记录；原文件将移入隔离目录')
            if body.get('revision') != state.get('revision'):
                reject(409, 'device_state_changed', '状态已变化，请刷新后重试')
            if state['running'] or self.app.busy or self.app.collector_running() or state['safety']['state'] not in ('offline', 'disarmed'):
                reject(409, 'device_cleanup_unsafe', '请先停止任务并确认下使能，再清理未完成记录')
            if not self.reporter:
                reject(503, 'device_reporting_unconfigured', '进度回写未配置，请联系管理员')
            from gello.data_utils.platform_progress import clear_incomplete
            result = clear_incomplete(self.reporter.outbox, self.app.profile.storage.root, session['claims']['batch_uuid'])
            self.app.revision += 1
            self.app.check_passed = False
            self.app.message = f"已清理 {result['quarantined']} 份未完成记录，原文件保留在隔离目录。请重新检查设备后开始采集。"
            self.app.notice_level = 'success'
            return result

    def complete_check(self, token, origin):
        with self.lock, self.app.lock:
            s = self.owned(token, origin)
            state = self.app.snapshot()
            if state['running'] or self.app.busy or self.app.collector_running():
                reject(409, 'device_still_running', '请先停止本机任务，再完成平台任务')
            if not self.reporter:
                reject(503, 'device_reporting_unconfigured', '请配置设备进度回写')
            progress = self.reporter.outbox.summary(s['claims']['batch_uuid'])
            if progress['pending'] or progress.get('unfinished'):
                reject(409, 'device_progress_pending', '本地进度尚未同步，请等待或联系管理员处理')
            return {'ready': True}

    def disconnect(self, token, origin):
        with self.lock, self.app.lock:
            self.owned(token, origin)
            status = self.app.snapshot()
            if status['running'] or self.app.busy or self.app.collector_running() or status['safety']['state'] not in ('offline', 'disarmed'):
                reject(409, 'device_still_enabled', '请先停止任务并确认下使能，再断开设备')
            self.app.device_owner = None
            self.session = None
            self.context.unlink(missing_ok=True)
            return {'disconnected': True}

    def open_stream(self, role, token, origin):
        with self.lock:
            self.owned(token, origin)
        if role not in ('main', 'left', 'right'):
            reject(404, 'not_found', '没有该相机')
        base = 'http://127.0.0.1:8765'
        req = urllib.request.Request(base+'/preview-session', headers={'X-YAM-Preview': '1'})
        with urllib.request.urlopen(req, timeout=2) as r:
            path = json.load(r)['path']
        if not re.fullmatch(r'/[A-Za-z0-9_-]+/', path):
            raise ValueError('Invalid local preview path')
        return urllib.request.urlopen(base+path+'stream/'+role, timeout=3)

    def frame(self, role, token, origin):
        with self.lock:
            self.owned(token, origin)
        if role not in ('main', 'left', 'right'):
            reject(404, 'not_found', '没有该相机')
        base = 'http://127.0.0.1:8765'
        req = urllib.request.Request(base+'/preview-session', headers={'X-YAM-Preview': '1'})
        with urllib.request.urlopen(req, timeout=2) as r:
            path = json.load(r)['path']
        if not re.fullmatch(r'/[A-Za-z0-9_-]+/', path):
            raise ValueError('Invalid local preview path')
        with urllib.request.urlopen(base+path+'frame/'+role, timeout=2) as r:
            return r.read(2*1024*1024)


def create_server(app, config, *, bridge=None, loopback=False, loopback_port=8773):
    origin_set = set(config['portal_origins'])
    address = urlsplit(config['url'])
    if address.scheme != 'https' or not address.hostname or address.path or address.query or address.fragment or address.username:
        raise ValueError('Device URL must be a trusted HTTPS origin')
    for origin in origin_set:
        u = urlsplit(origin)
        if u.scheme != 'https' or not u.hostname or u.path or u.query or u.fragment or u.username:
            raise ValueError('Portal origins must be exact HTTPS origins')
    if not origin_set:
        raise ValueError('Portal origins required')
    if config['station_id'] != app.profile.name:
        raise ValueError('station_id must match the active YAM profile name')
    bridge = bridge or DeviceBridge(app, config)
    if loopback:
        address = urlsplit(f'http://127.0.0.1:{loopback_port}')

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # Never log credentials or request bodies.

        def allowed(self):
            if self.headers.get('Origin') not in origin_set or self.headers.get('Host') != address.netloc:
                reject(403, 'device_origin_forbidden', '请从已授权的 RDP 域名连接此设备')

        def reply(self, status, data, mime='application/json'):
            raw = json.dumps(data, ensure_ascii=False).encode() if mime == 'application/json' else data
            self.send_response(status)
            origin = self.headers.get('Origin')
            if origin in origin_set:
                self.send_header('Access-Control-Allow-Origin', origin)
                self.send_header('Vary', 'Origin')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_OPTIONS(self):
            try:
                self.allowed()
                if self.headers.get('Access-Control-Request-Method') not in ('GET', 'POST'):
                    reject(403, 'device_origin_forbidden', '不允许该请求方法')
                self.send_response(204)
                self.send_header('Access-Control-Allow-Origin', self.headers['Origin'])
                self.send_header('Vary', 'Origin')
                self.send_header('Access-Control-Allow-Methods', 'GET, POST')
                self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-YAM-Device-Session')
                self.send_header('Access-Control-Allow-Private-Network', 'true')
                self.send_header('Access-Control-Max-Age', '300')
                self.end_headers()
            except DeviceError as e:
                self.reply(e.status, {'code': e.code, 'message': str(e)})

        def stream(self, role, token, origin):
            # Relay existing cached JPEG stream without re-encoding or a frame queue.
            with bridge.open_stream(role, token, origin) as upstream:
                self.connection.settimeout(2)
                self.send_response(200)
                self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=yamframe')
                self.send_header('Access-Control-Allow-Origin', origin)
                self.send_header('Vary', 'Origin')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Accel-Buffering', 'no')
                self.send_header('Connection', 'close')
                self.end_headers()
                self.close_connection = True
                try:
                    while True:
                        chunk = upstream.read1(65536)
                        if not chunk:
                            return
                        with bridge.lock:
                            bridge.owned(token, origin)
                        self.wfile.write(chunk)
                        self.wfile.flush()
                except (OSError, DeviceError):
                    return

        def handle_request(self, post=False):
            try:
                self.connection.settimeout(10)
                self.allowed()
                token = self.headers.get('X-YAM-Device-Session')
                origin = self.headers['Origin']
                data = {}
                if post:
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 0 < length <= 12*1024*1024 or self.headers.get('Content-Type') != 'application/json':
                        reject(400, 'invalid_device_request', '请求格式或大小不合法')
                    data = json.loads(self.rfile.read(length))
                    if not isinstance(data, dict):
                        raise ValueError('Request must be an object')
                if not post and self.path == '/v1/challenge':
                    result = bridge.challenge()
                elif not post and self.path == '/v1/status':
                    result = bridge.status(token, origin)
                elif not post and self.path.startswith('/v1/streams/'):
                    return self.stream(self.path.rsplit('/', 1)[-1], token, origin)
                elif not post and self.path.startswith('/v1/frames/'):
                    return self.reply(200, bridge.frame(self.path.rsplit('/', 1)[-1], token, origin), 'image/jpeg')
                elif post and self.path == '/v1/session':
                    result = bridge.connect(data, origin, token)
                elif post and self.path == '/v1/actions':
                    result = bridge.action(data, token, origin)
                elif post and self.path == '/v1/complete-check':
                    result = bridge.complete_check(token, origin)
                elif post and self.path == '/v1/disconnect':
                    result = bridge.disconnect(token, origin)
                else:
                    reject(404, 'not_found', '没有该设备接口')
                self.reply(200, result)
            except DeviceError as e:
                self.reply(e.status, {'code': e.code, 'message': str(e)})
            except (ValueError, KeyError, TypeError) as e:
                self.reply(409, {'code': 'device_state_changed', 'message': str(e)})
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception:
                self.reply(503, {'code': 'device_unavailable', 'message': '本地设备服务暂不可用，请检查连接后重试'})

        def do_GET(self): self.handle_request()
        def do_POST(self): self.handle_request(True)

    server = ThreadingHTTPServer(('127.0.0.1' if loopback else config.get('bind', '127.0.0.1'), address.port or 443), Handler)
    if not loopback:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(config['tls_cert'], config['tls_key'])
        server.socket = context.wrap_socket(server.socket, server_side=True)
    server.bridge = bridge
    return server
