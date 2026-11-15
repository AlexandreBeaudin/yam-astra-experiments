"""Release selection tests; no robot or camera is opened."""
import json
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from PIL import Image
from scripts.guided_collection import prepare
from gello.data_utils.real2sim_guidance import guidance_session_type

guidance_session_type()
from real2sim.releases import publish

@pytest.fixture
def setup(tmp_path):
    camera = dict(image_size=[8,6],serial='nuc-main',saved_image_rotation_deg=0)
    def bundle(name, **overrides):
        path=tmp_path/name;path.mkdir()
        data=dict(task=dict(platform='yam',task_id='TASK-YAM-TEST'),camera=camera,
                  calibration_status='verified',layouts=[dict(id='one',validation=dict(passed=True))])
        data.update(overrides)
        (path/'manifest.json').write_text(json.dumps(data))
        Image.new('RGBA',(8,6),(255,0,0,128)).save(path/'one.png')
        return path
    baseline=bundle('baseline');cfg=tmp_path/'guidance.json'
    cfg.write_text(json.dumps(dict(platform='yam',station_id='test-nuc',task_ids=['TASK-YAM-TEST'],
        calibration_bundle=str(baseline),registry=str(tmp_path/'registry'))))
    profile=SimpleNamespace(name='test-nuc',cameras=SimpleNamespace(config_path='unused'))
    cameras=SimpleNamespace(cameras={'main':SimpleNamespace(width=8,height=6,serial='nuc-main',rotation=0)})
    with patch('scripts.guided_collection.load_profile',return_value=profile), patch('scripts.guided_collection.load_camera_rig_config',return_value=cameras):
        yield tmp_path,cfg,bundle

def test_select_only_valid_published_version(setup):
    root,cfg,bundle=setup
    with pytest.raises(ValueError,match='没有匹配'): prepare('TASK-YAM-TEST',cfg)
    source=bundle('release');publish(root/'registry',source,'test-nuc')
    assert prepare('TASK-YAM-TEST',cfg)[0]==source
    Image.new('RGBA',(8,6),(0,0,255,128)).save(source/'one.png')
    with pytest.raises(ValueError,match='没有匹配'): prepare('TASK-YAM-TEST',cfg)

def test_reject_wrong_platform_task_camera_and_station(setup):
    root,cfg,bundle=setup
    for name,override in [
        ('platform',dict(task=dict(platform='xtrainer',task_id='TASK-YAM-TEST'))),
        ('task',dict(task=dict(platform='yam',task_id='other'))),
        ('camera',dict(camera=dict(image_size=[8,6],serial='other',saved_image_rotation_deg=0))),
        ('station',dict(station_id='other')),
        ('calibration',dict(calibration_status='missing')),
    ]:
        with pytest.raises(ValueError): prepare('TASK-YAM-TEST',cfg,bundle=bundle(name,**override))
