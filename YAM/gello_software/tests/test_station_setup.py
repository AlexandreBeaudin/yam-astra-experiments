"""Provisioning checks never send motor commands or modify the real workstation."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
import yaml
spec=importlib.util.spec_from_file_location('station_setup',Path(__file__).parents[1]/'gello/station_setup.py')
s=importlib.util.module_from_spec(spec);spec.loader.exec_module(s)

def test_binding_check_blocks_adapter_swap(tmp_path,monkeypatch):
    monkeypatch.setattr(s,'ROOT',tmp_path)
    (tmp_path/'setup').mkdir();(tmp_path/'setup/active.json').write_text(json.dumps({'arms':{'left':{'can':'can0','can_serial':'old','leader':'/dev/a'}}}))
    monkeypatch.setattr(s,'discover',lambda:dict(cans=[dict(channel='can0',serial='new')],leaders=[dict(path='/dev/a',readable=True)]))
    with pytest.raises(ValueError,match='CAN身份'):s.validate_bindings()

def test_revision_and_duplicate_mapping_rejected(monkeypatch):
    monkeypatch.setattr(s,'fingerprint',lambda:'current')
    with pytest.raises(ValueError,match='配置已变化'):s.plan({'revision':'stale'})
    monkeypatch.setattr(s,'discover',lambda:{})
    monkeypatch.setattr(s,'current',lambda:{})
    with pytest.raises(ValueError,match='重复绑定'):s.plan(dict(revision='current',arms={x:dict(leader='same',can=x) for x in ['left','right']},cameras=dict(main='1',left='2',right='3')))

def test_apply_requires_disarm_and_cleans_maintenance(tmp_path,monkeypatch):
    monkeypatch.setattr(s,'ROOT',tmp_path)
    def unsafe():raise ValueError('请先下使能')
    monkeypatch.setattr(s,'safe_to_configure',unsafe)
    with pytest.raises(ValueError,match='下使能'):s.apply({'confirmed':True})
    assert not (tmp_path/'setup/maintenance').exists()

def test_apply_versions_configuration_and_preserves_rollback(tmp_path,monkeypatch):
    monkeypatch.setattr(s,'ROOT',tmp_path)
    monkeypatch.setattr(s.Path,'home',lambda:tmp_path)
    default=tmp_path/'profiles/default.yaml';default.parent.mkdir();default.write_text('old profile')
    monkeypatch.setattr(s,'default_profile_path',lambda:default)
    monkeypatch.setattr(s,'safe_to_configure',lambda:None)
    def load(path=None):
        if path is None:raise ValueError('no prior profile')
        assert yaml.safe_load(path.read_text())['name']=='station'
    monkeypatch.setattr(s,'load_profile',load)
    monkeypatch.setattr(s,'plan',lambda _:dict(arms={'left':{},'right':{}},cameras=dict(main='1',left='2',right='3'),armconfigs={'left':{},'right':{}},config={'station_id':'station'},token='private-token',storage=str(tmp_path/'data')))
    calls=[];monkeypatch.setattr(s.subprocess,'run',lambda cmd,**_:calls.append(cmd))
    result=s.apply({'confirmed':True});directory=tmp_path/'setup'/result['version']
    assert (directory/'previous-profile.yaml').read_text()=='old profile'
    assert (directory/'report-token').stat().st_mode&0o777==0o600
    assert json.loads((tmp_path/'setup/active.json').read_text())['device_config']==str(directory/'device.json')
    assert calls[0][-2:]==['stop','yam-operator.service'] and calls[-1][-2:]==['start','yam-operator.service']
    assert not (tmp_path/'setup/maintenance').exists()
