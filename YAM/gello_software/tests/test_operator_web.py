"""Operator lifecycle invariants; never instantiate hardware."""
import importlib.util
from pathlib import Path
import time
from types import SimpleNamespace
from unittest.mock import Mock
import pytest

spec=importlib.util.spec_from_file_location('operator_web',Path(__file__).parents[1]/'scripts/operator_web.py')
web=importlib.util.module_from_spec(spec);spec.loader.exec_module(web)

@pytest.fixture
def app(tmp_path,monkeypatch):
    monkeypatch.setattr(web,'load_profile',lambda _:SimpleNamespace(network=SimpleNamespace(safety_port=6002),storage=SimpleNamespace(root=tmp_path)))
    monkeypatch.setattr(web.Path,'home',lambda:tmp_path)
    a=web.Workbench();a.safety=lambda:dict(state='safe_hold',session_id='session',hold_confirmed=True)
    a.collector_running=lambda:False
    return a

def finish(app):
    deadline=time.monotonic()+2
    while app.busy and time.monotonic()<deadline:time.sleep(.01)
    assert not app.busy

@pytest.mark.parametrize('action',['enable','off'])
def test_confirmation_required(app,action):
    with pytest.raises(ValueError,match='确认'):app.action(dict(action=action,revision=0))

def test_stale_revision_rejected(app):
    with pytest.raises(ValueError,match='状态'):app.action(dict(action='check',revision=9))

def test_disable_live_collector_blocked(app,monkeypatch):
    app.collector_running=lambda:True
    client=Mock();monkeypatch.setattr(web,'SafetyClient',client)
    app.action(dict(action='off',revision=0,confirmed=True));finish(app)
    assert '先停止' in app.message;client.assert_not_called()

def test_disable_acknowledged(app,monkeypatch):
    c=Mock();c.disarm.return_value=dict(state='disarmed')
    monkeypatch.setattr(web,'SafetyClient',lambda **_:c)
    app.action(dict(action='off',revision=0,confirmed=True));finish(app)
    c.disarm.assert_called_once_with('session','off');assert '已确认' in app.message

def test_stop_transport_failure_still_requests_hold(app,monkeypatch):
    app.collection=Mock(side_effect=RuntimeError('offline'));c=Mock()
    monkeypatch.setattr(web,'SafetyClient',lambda **_:c)
    app.action(dict(action='stop',revision=0));finish(app)
    c.safe_hold.assert_called_once();assert '未确认' in app.message

@pytest.mark.parametrize('task',['../bad','a;touch /tmp/oops',''])
def test_task_names_not_shell_input(app,task):
    with pytest.raises(ValueError):app.start(task,False)

def test_active_session_not_taken_over(app):
    app.safety=lambda:dict(state='active')
    with pytest.raises(ValueError,match='不允许'):app.start('valid',False)
