"""Offline MuJoCo smoke tests and three view renders; no robot or grasp claims."""
import os
os.environ.setdefault('MUJOCO_GL','egl')
import xml.etree.ElementTree as ET
import numpy as np
import mujoco
from PIL import Image
from scipy.spatial.transform import Rotation


def validate_and_render(out, spec, visuals):
    tree=ET.parse(out/'asset.mjcf.xml');root=tree.getroot();world=root.find('worldbody')
    ET.SubElement(world,'geom',name='ground',type='plane',size='1 1 .01',rgba='.27 .30 .34 1',friction='.5 .005 .0001')
    ET.SubElement(world,'light',pos='0 -.5 1',dir='0 .3 -1',diffuse='.85 .85 .85',ambient='.35 .35 .35')
    ET.SubElement(root.find('visual'),'headlight',ambient='.3 .3 .3',diffuse='.6 .6 .6')
    ET.SubElement(root.find('visual'),'global',offwidth='512',offheight='512')
    tree.write(out/'validation_scene.xml',encoding='unicode')
    model=mujoco.MjModel.from_xml_path(str(out/'validation_scene.xml'));data=mujoco.MjData(model)
    vertices=np.concatenate([m.vertices for m,_ in visuals]);trials=[]
    for angle in [0,5,-5]:
        mujoco.mj_resetData(model,data)
        rot=Rotation.from_euler('x',angle,degrees=True);q=rot.as_quat()
        data.qpos[:3]=[0,0,.015-(vertices@rot.as_matrix().T)[:,2].min()]
        data.qpos[3:7]=q[[3,0,1,2]]
        for _ in range(480):mujoco.mj_step(model,data)
        before=data.qpos[:3].copy()
        for _ in range(120):mujoco.mj_step(model,data)
        mujoco.mj_forward(model,data)
        matrix=data.xmat[1].reshape(3,3);pos=data.xpos[1]
        bottom=float((vertices@matrix.T+pos)[:,2].min());drift=float(np.linalg.norm(data.qpos[:3]-before))
        tilt=float(np.rad2deg(np.arccos(np.clip(matrix[2,2],-1,1))))
        passed=bool(np.isfinite(data.qpos).all() and abs(bottom)<.002 and drift<.001 and (spec['kind'] in ['ball','sausage'] or tilt<2))
        trials.append({'initial_tilt_deg':angle,'passed':passed,'bottom_z_m':bottom,'final_half_second_drift_m':drift,
                       'final_tilt_deg':tilt,'qpos':data.qpos.tolist()})
    # Render the canonical asset pose for geometric inspection, separate from settling results.
    mujoco.mj_resetData(model,data);data.qpos[3]=1;mujoco.mj_forward(model,data)
    size=np.array(spec['dimensions_mm'])/1000
    camera=mujoco.MjvCamera();camera.lookat[:]=[0,0,size[2]/2];camera.distance=float(max(size)*2.8)
    options=mujoco.MjvOption();options.geomgroup[3]=0
    renders=[]
    with mujoco.Renderer(model,height=512,width=512) as renderer:
        for name,azimuth,elevation in [('oblique',125,-25),('front',90,-5),('top',90,-89.9)]:
            camera.azimuth=azimuth;camera.elevation=elevation
            renderer.update_scene(data,camera,scene_option=options);image=renderer.render()
            if image.shape!=(512,512,3) or float(image.std())<1:raise ValueError('Invalid rendered frame')
            Image.fromarray(image).save(out/(name+'.png'));renders.append(name+'.png')
    return {'backend':'MuJoCo','backend_version':mujoco.__version__,'hz':240,'seconds':2.5,
            'passed':all(t['passed'] for t in trials),'trials':trials,'renders':renders,
            'render_pose':'canonical, not final settled poses','grasp_tested':False,'physx_tested':False}
