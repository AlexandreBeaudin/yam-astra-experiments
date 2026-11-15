import json
import os
import time
import urllib.error
import pytest
from gello.data_utils.platform_progress import Outbox, EpisodeProgress
from gello.data_utils.platform_guidance import PlatformGuidance


def setup(tmp_path):
    c = tmp_path / 'context.json'
    c.write_text(json.dumps(dict(batch_uuid='batch', aud='station', _report_grant='secret',
                               _grant_expires=time.time()+120, source='rdp', guidance_kind='reference')))
    o = Outbox(tmp_path / 'queue.db')
    p = EpisodeProgress(c, o)
    p.start()
    return p, o, c


def test_offline_restart_duplicate_ack_and_published_inode(tmp_path):
    p,o,c=setup(tmp_path)
    src=tmp_path/'partial'; src.write_bytes(b'mcap')
    target=tmp_path/'saved'
    p.before_commit(src,target,'success',0,5)
    assert not o.flush(lambda payload: (_ for _ in ()).throw(OSError('offline')))
    assert o.summary('batch')['pending']==2
    got=[]
    o=Outbox(o.path)
    assert o.flush(got.append)
    assert not o.flush(got.append)
    os.link(src,target);src.unlink()
    assert o.flush(got.append)
    assert [v['event']['kind'] for v in got]==['started','saved']
    assert not o.flush(got.append)
    assert o.summary('batch')['pending']==0
    assert 'secret' not in json.dumps(PlatformGuidance(c).snapshot())


def test_failed_publication_can_be_discarded_not_counted(tmp_path):
    p,o,_=setup(tmp_path)
    src=tmp_path/'partial';src.write_bytes(b'x')
    p.before_commit(src,tmp_path/'target','fail',0,0)
    p.cancel_unpublished();p.discard();p.discard()
    got=[]
    while o.flush(got.append):pass
    assert [v['event']['kind'] for v in got]==['started','discarded']


def test_wrong_inode_never_acknowledged_and_published_cannot_discard(tmp_path):
    p,o,_=setup(tmp_path)
    src=tmp_path/'partial';src.write_bytes(b'x')
    target=tmp_path/'target'; target.write_bytes(b'other')
    p.before_commit(src,target,'success',0,0)
    assert o.flush(lambda _:None)
    assert not o.flush(lambda _:pytest.fail('wrong file reported'))
    with pytest.raises(ValueError):p.cancel_unpublished()
    assert o.summary('batch')['pending']==1


def test_rejected_event_retained_and_same_event_retries(tmp_path):
    p,o,_=setup(tmp_path)
    ids=[]
    def rejected(payload):
        ids.append(payload['event']['event_id'])
        raise urllib.error.HTTPError('url',409,'conflict',{},None)
    assert not o.flush(rejected)
    assert o.summary('batch')['blocked']==1
    def accept(payload):ids.append(payload['event']['event_id'])
    o.retry_after.clear()
    assert o.flush(accept)
    assert ids[0]==ids[1]
    assert o.summary('batch')['blocked']==0


def test_context_immutable_and_expired_start_rejected(tmp_path):
    p,o,c=setup(tmp_path)
    c.write_text(json.dumps(dict(_grant_expires=0,_report_grant='old')))
    p.discard()
    got=[]
    while o.flush(got.append):pass
    assert got[-1]['batch_uuid']=='batch'
    with pytest.raises(ValueError):p.start()


def test_real_mcap_writer_reports_after_publication_only(tmp_path):
    import numpy as np
    from gello.data_utils.mcap_recording import McapEpisodeWriter
    from gello.data_utils.mcap_tools import messages
    _,o,c=setup(tmp_path)
    # Separate clean outbox; the helper's started event is unrelated to this recording.
    o=Outbox(tmp_path/'writer.db')
    writer=McapEpisodeWriter(tmp_path/'data','task',200,image_encoding='raw+zstd')
    writer.guidance=PlatformGuidance(c)
    writer.platform_progress=EpisodeProgress(c,o)
    try:
        writer.start()
        writer.append({},np.zeros(14),0,leader_action=np.zeros(14))
        path=writer.commit('success')
        got=[]
        while o.flush(got.append): pass
        assert path.exists()
        assert [x['event']['kind'] for x in got]==['started','saved']
        assert o.summary('batch')['unfinished']==0
        assert o.summary('batch')['pending']==0
    finally:
        writer.close()


def test_abnormal_exit_quarantine_closes_orphan_and_retry_is_safe(tmp_path):
    import uuid
    from gello.data_utils.platform_progress import clear_incomplete
    batch=str(uuid.uuid4())
    context=tmp_path/'context.json'
    context.write_text(json.dumps(dict(batch_uuid=batch,aud='nuc',_report_grant='signed',_grant_expires=time.time()+120)))
    o=Outbox(tmp_path/'queue.db');p=EpisodeProgress(context,o);p.start()
    root=tmp_path/('RDP-'+batch)/'mcap'
    (root/'.in_progress').mkdir(parents=True)
    partial=root/'.in_progress/episode_0001_x.mcap';partial.write_bytes(b'partial')
    (root/'success').mkdir();saved=root/'success/episode_0002_y.mcap';saved.write_bytes(b'saved')
    result=clear_incomplete(o,tmp_path,batch)
    assert result['quarantined']==1 and result['closed_episodes']==1
    assert (root/'.discarded'/partial.name).read_bytes()==b'partial'
    assert saved.read_bytes()==b'saved'
    assert o.summary(batch)['unfinished']==0
    assert clear_incomplete(o,tmp_path,batch)['closed_episodes']==0
    got=[]
    while o.flush(got.append): pass
    assert [v['event']['kind'] for v in got]==['started','discarded']


def test_cleanup_after_move_crash_and_failed_publish(tmp_path):
    import uuid
    from gello.data_utils.platform_progress import clear_incomplete
    batch=str(uuid.uuid4());c=tmp_path/'context.json'
    c.write_text(json.dumps(dict(batch_uuid=batch,aud='nuc',_report_grant='signed',_grant_expires=time.time()+120)))
    o=Outbox(tmp_path/'queue.db');p=EpisodeProgress(c,o);p.start()
    root=tmp_path/('RDP-'+batch)/'mcap';(root/'.in_progress').mkdir(parents=True);(root/'.discarded').mkdir()
    src=root/'.in_progress/a.mcap';src.write_bytes(b'partial')
    p.before_commit(src,root/'fail/a.mcap','fail',0,0)
    os.rename(src,root/'.discarded/a.mcap')  # crash between filesystem and DB commit
    assert o.summary(batch)['unfinished']==1  # UI must still offer cleanup after restart
    assert clear_incomplete(o,tmp_path,batch)['closed_episodes']==1
    got=[]
    while o.flush(got.append):pass
    assert [x['event']['kind'] for x in got]==['started','discarded']


def test_strategy_advances_only_after_success_is_published_even_offline(tmp_path):
    import base64
    p,o,c=setup(tmp_path)
    context=json.loads(c.read_text())
    for ordinal, outcome in [(1, 'success'), (2, 'fail')]:
        claims=base64.urlsafe_b64encode(json.dumps({'ordinal':ordinal}).encode()).decode().rstrip('=')
        context['_report_grant']='rdp-device-v1.'+claims+'.test'
        c.write_text(json.dumps(context))
        p=EpisodeProgress(c,o);p.start()
        src=tmp_path/f'partial-{ordinal}';src.write_bytes(b'mcap')
        target=tmp_path/f'saved-{ordinal}'
        p.before_commit(src,target,outcome,0,5)
        assert o.completed_ordinals('batch') == ([] if ordinal==1 else [1])
        os.link(src,target);src.unlink()
        assert o.completed_ordinals('batch') == [1]
    p=EpisodeProgress(c,o);p.start();p.discard()
    assert o.completed_ordinals('batch') == [1]
    assert o.completed_ordinals('other-batch') == []
    while o.flush(lambda _:None):pass
    assert Outbox(o.path).completed_ordinals('batch') == [1]
