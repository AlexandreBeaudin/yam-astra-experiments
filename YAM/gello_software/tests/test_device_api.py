"""Protocol/lifecycle tests with no hardware imports or motor commands."""
import base64
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import threading
from types import SimpleNamespace
from unittest.mock import Mock

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from PIL import Image
import pytest

spec = importlib.util.spec_from_file_location('device_api', Path(__file__).parents[1] / 'scripts/device_api.py')
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)
ORIGIN = 'https://portal.example.test'

@pytest.fixture
def rig(tmp_path):
    key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    pub = base64.b64encode(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    state = dict(running=False, busy=False, check_passed=True, safety=dict(state='offline'))
    app = Mock(profile=SimpleNamespace(storage=SimpleNamespace(root=tmp_path)), lock=threading.RLock(), busy=False, device_owner=None)
    app.snapshot.side_effect = lambda: dict(state)
    app.collector_running.return_value = False
    app.collection.return_value = dict(phase='ready', owns_control=True, pending=False)
    now = [1000.]
    bridge = api.DeviceBridge(app, dict(public_key=pub, device_id='nuc', station_id='yam'), lambda: now[0])
    bridge.context = tmp_path / 'context.json'
    img = io.BytesIO(); Image.new('RGB', (8, 6)).save(img, 'PNG'); raw = img.getvalue()
    def body(**changes):
        claims = dict(iss='rhosdata-platform/device-v1', aud='nuc', sub='person', origin=ORIGIN,
                      station_id='yam', nonce=bridge.challenge()['nonce'], iat=int(now[0]), exp=int(now[0])+120,
                      batch_uuid='12345678-1234-1234-1234-123456789abc', task_id='task', strategy_id='strategy', ordinal=1,
                      guidance_kind='reference', image_sha256=hashlib.sha256(raw).hexdigest(), image_width=8, image_height=6)
        claims.update(changes)
        unsigned = 'rdp-device-v1.' + base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip('=')
        token = unsigned + '.' + base64.urlsafe_b64encode(key.sign(unsigned.encode())).decode().rstrip('=')
        return dict(grant=token, image_base64=base64.b64encode(raw).decode())
    return bridge, app, state, now, body

def test_signed_context_no_credentials_and_nonce_single_use(rig):
    b, app, _, _, body = rig; data = body()
    s = b.connect(data, ORIGIN)
    saved = json.loads(b.context.read_text())
    assert saved['calibration_status'] == 'unverified_reference'
    assert 'grant' not in saved and 'session' not in saved
    assert b.context.stat().st_mode & 0o777 == 0o600
    with pytest.raises(api.DeviceError): b.connect(data, ORIGIN, s['session'])
    app.action.assert_not_called()

@pytest.mark.parametrize('changes', [dict(aud='other'), dict(station_id='other'), dict(origin='https://evil.test'), dict(exp=999), dict(exp=5000), dict(guidance_kind='calibrated'), dict(ordinal=0)])
def test_mismatched_grant_rejected(rig, changes):
    b, app, _, _, body = rig
    with pytest.raises(api.DeviceError): b.connect(body(**changes), ORIGIN)
    app.action.assert_not_called()

def test_wrong_image_and_signature_rejected(rig):
    b, _, _, _, body = rig
    data = body(); data['image_base64'] = base64.b64encode(b'not image').decode()
    with pytest.raises(api.DeviceError, match='图片'): b.connect(data, ORIGIN)
    data = body(); data['grant'] = data['grant'][:-12] + 'A'*12
    with pytest.raises(api.DeviceError): b.connect(data, ORIGIN)

def test_expiry_preserves_conclusion_but_blocks_new_motion(rig):
    b, app, _, now, body = rig; s = b.connect(body(), ORIGIN)['session']; now[0] += 121
    for action in ('check', 'enable', 'start'):
        with pytest.raises(api.DeviceError): b.action(dict(action=action), s, ORIGIN)
    for action in ('stop', 'off', 'finish', 'success', 'fail', 'discard'):
        b.action(dict(action=action, revision=0, collection_revision=0, confirmed=True), s, ORIGIN)
    assert app.action.call_count == 2
    assert app.collection.call_count == 4

def test_no_takeover_or_strategy_change_during_recording(rig):
    b, app, state, _, body = rig; s = b.connect(body(), ORIGIN)['session']
    state['running'] = True; app.collection.return_value = dict(phase='recording')
    with pytest.raises(api.DeviceError): b.connect(body(), ORIGIN, 'other-token')
    with pytest.raises(api.DeviceError): b.connect(body(strategy_id='other'), ORIGIN, s)
    with pytest.raises(api.DeviceError): b.disconnect(s, ORIGIN)
    # Same strategy renews while recording, including identity metadata.
    assert b.connect(body(), ORIGIN, s)['session'] == s

def test_check_and_revision_required(rig):
    b, app, state, _, body = rig; s = b.connect(body(), ORIGIN)['session']
    state['check_passed'] = False
    with pytest.raises(api.DeviceError): b.action(dict(action='enable', reference_confirmed=True), s, ORIGIN)
    with pytest.raises(api.DeviceError): b.action(dict(action='start'), s, ORIGIN)
    app.action.assert_not_called(); app.collection.assert_not_called()

def test_lost_session_recovery_requires_disarm(rig):
    b, _, state, now, body = rig; old = b.connect(body(), ORIGIN)['session']; now[0] += 121
    state['safety'] = dict(state='safe_hold')
    with pytest.raises(api.DeviceError): b.connect(body(), ORIGIN)
    state['safety'] = dict(state='disarmed')
    new = b.connect(body(), ORIGIN)['session']; assert new != old
    with pytest.raises(api.DeviceError): b.status(old, ORIGIN)
    assert b.disconnect(new, ORIGIN)['disconnected']

def test_stale_challenge(rig):
    b, _, _, now, body = rig; data = body(); now[0] += 61
    with pytest.raises(api.DeviceError): b.connect(data, ORIGIN)

def test_shared_go_python_signature_vector(rig):
    b, _, _, _, _ = rig
    data = json.loads((Path(__file__).parent/'fixtures/rdp_device_grant.json').read_text())
    b.challenges['a'*43] = 1000
    assert b.connect(data, ORIGIN)['expires_at'] == 1120


def test_pending_start_blocks_strategy_change(rig):
    b, app, state, _, body = rig
    token = b.connect(body(), ORIGIN)['session']
    state['running'] = True
    app.collection.return_value = dict(phase='ready', owns_control=True, pending=True)
    with pytest.raises(api.DeviceError): b.connect(body(strategy_id='next'), ORIGIN, token)
    assert json.loads(b.context.read_text())['strategy_id'] == 'strategy'


def test_completion_requires_stopped_drained_and_finished(rig):
    b, app, state, _, body = rig
    token = b.connect(body(), ORIGIN)['session']
    b.reporter = Mock()
    b.reporter.outbox.summary.return_value = dict(pending=0, unfinished=0)
    assert b.complete_check(token, ORIGIN)['ready']
    state['running'] = True
    with pytest.raises(api.DeviceError, match='停止'): b.complete_check(token, ORIGIN)
    state['running'] = False
    for values in (dict(pending=1, unfinished=0), dict(pending=0, unfinished=1)):
        b.reporter.outbox.summary.return_value = values
        with pytest.raises(api.DeviceError, match='同步'): b.complete_check(token, ORIGIN)


def test_missing_reporting_config_blocks_enable_not_safe_exit(rig):
    b, app, _, _, body = rig
    token=b.connect(body(), ORIGIN)['session']
    with pytest.raises(api.DeviceError, match='回写'):
        b.action(dict(action='enable',confirmed=True,reference_confirmed=True),token,ORIGIN)
    b.action(dict(action='stop',confirmed=True),token,ORIGIN)
    b.action(dict(action='off',confirmed=True),token,ORIGIN)
    assert app.action.call_count == 2


def test_cleanup_requires_disarm_confirmation_and_current_revision(rig):
    b,app,state,_,body=rig
    state['revision']=3
    token=b.connect(body(),ORIGIN)['session']
    for safety,confirmed,revision in [('active',True,3),('safe_hold',True,3),('offline',False,3),('offline',True,2)]:
        state['safety']['state']=safety
        with pytest.raises(api.DeviceError):
            b.action(dict(action='clear_incomplete',confirmed=confirmed,revision=revision),token,ORIGIN)
    app.action.assert_not_called()


def test_initializing_never_claims_stale_collector(rig):
    b, app, state, _, body=rig
    s=b.connect(body(),ORIGIN)['session']
    state.update(running=True,phase='initializing',task='new-task')
    app.collection.return_value=dict(phase='waiting_for_trigger',task='old-task')
    assert b.status(s,ORIGIN)['collection'] is None
    app.collection.assert_not_called()
    state['phase']='ready'
    assert b.status(s,ORIGIN)['collection'] is None
    app.collection.return_value=dict(phase='ready',task='new-task')
    assert b.status(s,ORIGIN)['collection']['phase']=='ready'


def test_completed_strategy_cannot_start_again(rig):
    b, app, _, _, body=rig
    s=b.connect(body(),ORIGIN)['session']
    b.reporter=SimpleNamespace(outbox=SimpleNamespace(completed_ordinals=lambda _:[1]))
    with pytest.raises(api.DeviceError, match='已保存成功'):
        b.action(dict(action='start',collection_revision=0),s,ORIGIN)
    app.collection.assert_not_called()


def test_stream_rejects_unowned_session_and_unknown_role(rig):
    b, _, _, _, body=rig
    s=b.connect(body(),ORIGIN)['session']
    with pytest.raises(api.DeviceError):b.open_stream('main','other',ORIGIN)
    with pytest.raises(api.DeviceError):b.open_stream('unknown',s,ORIGIN)


def test_loopback_server_shares_ownership_and_restricts_origin_host(rig):
    import socket
    import urllib.request
    import urllib.error
    b,app,_,_,body=rig
    app.profile.name='yam'
    cfg={**b.config,'url':'https://device.example:8771','portal_origins':[ORIGIN],'bind':'0.0.0.0'}
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    server=api.create_server(app,cfg,bridge=b,loopback=True,loopback_port=port)
    assert server.server_address[0]=='127.0.0.1'
    threading.Thread(target=server.serve_forever,daemon=True).start()
    op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    base=f'http://127.0.0.1:{port}'
    try:
        session=b.connect(body(),ORIGIN)['session']
        req=urllib.request.Request(base+'/v1/status',headers={'Origin':ORIGIN,'X-YAM-Device-Session':session})
        assert json.load(op.open(req))['device_id']=='nuc'
        for headers in [{'Origin':'https://evil.test'},{'Origin':ORIGIN,'Host':'evil.test'}]:
            with pytest.raises(urllib.error.HTTPError) as error:op.open(urllib.request.Request(base+'/v1/challenge',headers=headers))
            assert error.value.code==403
        req=urllib.request.Request(base+'/v1/status',headers={'Origin':ORIGIN,'X-YAM-Device-Session':'other'})
        with pytest.raises(urllib.error.HTTPError) as error:op.open(req)
        assert error.value.code==403
    finally:server.shutdown();server.server_close()

def test_cancel_before_check_and_guard_untracked_collector(rig):
    b, app, state, _, body = rig
    state['check_passed'] = False
    session = b.connect(body(), ORIGIN)['session']
    app.collector_running.return_value = True
    with pytest.raises(api.DeviceError):
        b.disconnect(session, ORIGIN)
    assert b.status(session, ORIGIN)
    app.collector_running.return_value = False
    assert b.disconnect(session, ORIGIN)['disconnected']
    app.action.assert_not_called()
