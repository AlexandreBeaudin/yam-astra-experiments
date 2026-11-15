"""Explicit real HTTPS/API/PostgreSQL test using a virtual task and synthetic MCAP.
No device API or hardware calls. RDP_TEST_STATE points to private local test config.
"""
import base64
import http.cookiejar
import json
import os
from pathlib import Path
import tempfile
import urllib.request
import urllib.error
import numpy as np
from gello.data_utils.platform_progress import EpisodeProgress, Outbox, Reporter
from gello.data_utils.platform_guidance import PlatformGuidance
from gello.data_utils.mcap_recording import McapEpisodeWriter

root = Path(os.environ['RDP_TEST_STATE'])
accounts = json.loads((root/'test-accounts.json').read_text())
task = json.loads((root/'progress-task.json').read_text())
config = json.loads((root/'device.json').read_text())
assert task['task_id'].startswith('YAM-PROGRESS-SOFTWARE-TEST-')
a = accounts['collector']
assert a['email'].endswith('@rdp-test.invalid')
origin = config['report_url'].split('/api/')[0]
jar = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(jar))
def call(path, body=None):
    req = urllib.request.Request(origin+'/api'+path, None if body is None else json.dumps(body).encode(),
                                 {'Content-Type':'application/json','Origin':origin})
    with op.open(req, timeout=15) as r:
        return json.load(r)
call('/auth/login', dict(email=a['email'], password=a['password']))
base = '/ops/teleop-batches/'+task['batch_uuid']
before = call(base+'/collection')
with tempfile.TemporaryDirectory() as tmp:
    d = Path(tmp)
    grant = call(base+'/device-grant', dict(device_id=config['device_id'], nonce='x'*43, ordinal=1))['grant']
    payload = grant.split('.')[1]
    claims = json.loads(base64.urlsafe_b64decode(payload+'='*(-len(payload)%4)))
    ctx = {k:claims[k] for k in ('sub','batch_uuid','task_id','aud','station_id','strategy_id','ordinal','image_sha256')}
    ctx.update(source='rdp', guidance_kind='reference', _report_grant=grant, _grant_expires=claims['exp'])
    context = d/'context.json'; context.write_text(json.dumps(ctx))
    outbox = Outbox(d/'outbox.sqlite3')
    reporter = Reporter(config, outbox)
    for outcome in ('fail','success'):
        writer = McapEpisodeWriter(d/'records','software-test',200,image_encoding='raw+zstd')
        writer.guidance = PlatformGuidance(context)
        writer.platform_progress = EpisodeProgress(context, outbox)
        try:
            writer.start()
            writer.append({},np.zeros(14),0,leader_action=np.zeros(14))
            saved = writer.commit(outcome)
            assert saved.exists()
        finally:
            writer.close()
    # Simulated network failure is retained; sender restart must recover four events.
    assert not outbox.flush(lambda _: (_ for _ in ()).throw(OSError('offline')))
    outbox = Outbox(outbox.path)
    receipts = []
    def send(value):
        assert set(value)=={'batch_uuid','device_id','grant','event'}
        receipts.append(value)
        reporter.send(value)
    while outbox.flush(send):
        pass
    assert outbox.summary(task['batch_uuid'])['pending']==0, outbox.summary(task['batch_uuid'])
    for _ in range(3):
        for receipt in receipts: reporter.send(receipt)
    after = call(base+'/collection')
    assert after['success_count']==before['success_count']+1, after
    assert after['failure_count']==before['failure_count']+1, after
    assert after['episodes'][0]['completed']
    # Browser cookie alone cannot authorize the station receipt endpoint.
    try:
        call('/device/collection-events',{k:receipts[0][k] for k in ('device_id','grant','event')})
        raise AssertionError('cookie accepted as station credential')
    except urllib.error.HTTPError as e:
        assert e.code==401
    report={'result':'passed','real_https':True,'real_postgres':True,'synthetic_mcap':True,
            'hardware_commands':0,'saved_success_delta':1,'saved_failure_delta':1,
            'duplicate_replays':12,'offline_restart':True,'cookie_cannot_report':True}
    (root/'progress-integration-report.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report))
