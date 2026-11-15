"""Calibration/projection boundary tests without camera or motor access."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import xml.etree.ElementTree as ET
import numpy as np
import pytest
from PIL import Image

def module(name):
    spec=importlib.util.spec_from_file_location(name,Path(__file__).parents[1]/'scripts'/f'{name}.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
builder=module('build_abc_calibration');preview=module('preview_abc_guidance')

def test_quaternion_translation_and_optical_axis():
    n=ET.fromstring('<body pos="1 2 3" quat="2 0 0 0"/>');T=builder.local(n)
    np.testing.assert_allclose(T[:3,:3],np.eye(3));np.testing.assert_allclose(T[:3,3],[1,2,3])
    np.testing.assert_allclose((T@builder.CONVERSION)[:3,2],[0,0,-1])
    with pytest.raises(ValueError):builder.local(ET.fromstring('<body euler="1 2 3"/>'))

def test_hierarchical_camera_and_wrist_mount(tmp_path):
    source=tmp_path/'scene.xml'
    source.write_text('''<mujoco><worldbody><body pos="0 0 1"><body pos=".1 0 .2"><camera name="top" fovy="58"/></body></body>
    <body name="left_arm" pos="0 .31 .75"><body name="left_link_6" pos="0 0 .3"><body pos=".02 0 .03"><camera name="left" fovy="58"/></body></body></body>
    <body name="right_arm" pos="0 -.31 .75"><body name="right_link_6" pos="0 0 .3"><body pos=".02 0 .03"><camera name="right" fovy="58"/></body></body></body>
    <geom name="table_plane" pos=".6 0 .75" size=".2975 .65 .01"/></worldbody></mujoco>''')
    c=builder.extract(source,'test')
    np.testing.assert_allclose(np.array(c['cameras']['main']['world_T_camera'])[:3,3],[.1,0,1.2])
    np.testing.assert_allclose(np.array(c['cameras']['left']['robot_mount']['link_T_camera'])[:3,3],[.02,0,.03])
    assert c['cameras']['main']['K'][0][0]==pytest.approx(432.971461265)
    assert c['calibration_status']=='simulated'
    assert 'world_T_camera' not in c['cameras']['left']

def test_simulated_preview_is_not_production_acceptance(tmp_path):
    from gello.data_utils.real2sim_guidance import session
    camera=SimpleNamespace(serial='test-main',width=640,height=480,rotation=0)
    config=SimpleNamespace(cameras={'main':camera})
    doc=dict(task=dict(platform='yam',task_id='ABC-TEST'),calibration_status='simulated',simulation_only=True,
        source=dict(repository='https://github.com/amazon-far/abc'),camera=dict(serial='test-main',image_size=[640,480],saved_image_rotation_deg=0),layouts=[dict(id='one')])
    (tmp_path/'manifest.json').write_text(json.dumps(doc));Image.new('RGBA',(640,480),(1,2,3,255)).save(tmp_path/'one.png')
    assert preview.test_guidance(tmp_path,config).state()['count']==1
    with pytest.raises(ValueError,match='verified'):session(tmp_path,config)
    camera.serial='other'
    with pytest.raises(ValueError,match='mismatch'):preview.test_guidance(tmp_path,config)
    camera.serial='test-main';(tmp_path/'RECALLED.json').write_text('{}')
    with pytest.raises(ValueError,match='recalled'):preview.test_guidance(tmp_path,config)
