"""Extract analytic calibration from a pinned ABC MJCF; no simulator or hardware started."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import xml.etree.ElementTree as ET
import numpy as np
from PIL import Image, ImageDraw

CONVERSION=np.diag([1.,-1.,-1.,1.])  # MJCF camera: -Z forward, +Y up -> optical +Z forward, +Y down.

def local(node):
    if any(k in node.attrib for k in ('euler','axisangle','xyaxes','zaxis')):
        raise ValueError('Selected chain requires a rotation format not implemented: '+str(node.attrib))
    q=np.fromstring(node.get('quat','1 0 0 0'),sep=' ');q/=np.linalg.norm(q);w,x,y,z=q
    T=np.eye(4);T[:3,3]=np.fromstring(node.get('pos','0 0 0'),sep=' ')
    T[:3,:3]=[[1-2*(y*y+z*z),2*(x*y-z*w),2*(x*z+y*w)],
               [2*(x*y+z*w),1-2*(x*x+z*z),2*(y*z-x*w)],
               [2*(x*z-y*w),2*(y*z+x*w),1-2*(x*x+y*y)]]
    return T

def extract(source,sha,serials=None,station_id="abc-simulated"):
    serials=serials or {role:"SIMULATED-"+role for role in ("main","left","right")}
    tree=ET.parse(source);root=tree.getroot();parents={child:parent for parent in root.iter() for child in parent}
    def node(tag,name):
        found=root.find(f'.//{tag}[@name="{name}"]')
        if found is None:raise ValueError('Missing '+name)
        return found
    def world(element):
        chain=[]
        while element.tag!='worldbody':
            if element.tag in ('body','camera','geom'):chain.append(element)
            element=parents[element]
        T=np.eye(4)
        for child in reversed(chain):T=T@local(child)
        return T
    cameras={}
    for role,name in [('main','top'),('left','left'),('right','right')]:
        cam=node('camera',name);fovy=float(cam.get('fovy'));fy=480/(2*np.tan(np.deg2rad(fovy)/2))
        data=dict(model='D405',serial=serials[role],source_camera=name,image_size=[640,480],fps=30,saved_image_rotation_deg=0,
            K=[[fy,0,320.],[0,fy,240.],[0,0,1]],distortion=[0.,0.,0.,0.,0.],distortion_model='none',fovy_deg=fovy,
            intrinsics_source='Derived from ABC MJCF fovy at 640x480, ideal square pixels; not physical D405 calibration',
            optical_frame='x right, y down, z forward',simulation_only=True)
        Tw=world(cam)@CONVERSION
        if role=='main':data['world_T_camera']=Tw.tolist();data['camera_T_world']=np.linalg.inv(Tw).tolist()
        else:
            flange=node('body',role+'_link_6');data['robot_mount']=dict(arm=role,link=role+'_link_6',link_T_camera=(np.linalg.inv(world(flange))@Tw).tolist(),runtime_world_pose_requires_FK=True)
        cameras[role]=data
    plane=node('geom','table_plane');size=np.fromstring(plane.get('size'),sep=' ');pos=world(plane)[:3,3]
    robots={f'world_T_{role}_base':world(node('body',role+'_arm')).tolist() for role in ['left','right']}
    return dict(schema_version=1,platform_id='yam',station_id=station_id,world_frame='abc_world',calibration_status='simulated',calibration_version='ABC-D405-'+sha[:12],simulation_only=True,
        source=dict(repository='https://github.com/amazon-far/abc',commit=sha,path='abc_sim/models/yam_bimanual_empty.xml',sha256=hashlib.sha256(source.read_bytes()).hexdigest()),
        cameras=cameras,robot=robots,table=dict(center_world_m=pos.tolist(),normal_world=[0.,0.,1.],plane_d_m=-pos[2],bounds_xy_m=[[pos[0]-size[0],pos[0]+size[0]],[pos[1]-size[1],pos[1]+size[1]]]),
        assumptions=['Real station follows this exact ABC standard scene, no camera randomization','No image rotation, ideal zero distortion, not factory/measured intrinsics','Wrist extrinsics are relative to ABC link_6; no fixed world wrist pose','Analytic projection only; no physics, task success or real field acceptance'])

def build(repo,output,serials,station_id="abc-simulated"):
    if output.exists():raise ValueError('Output exists; choose a new version')
    source=repo/'abc_sim/models/yam_bimanual_empty.xml';sha=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip();station=extract(source,sha,serials,station_id)
    cam=station['cameras']['main'];K=np.array(cam['K']);W=np.array(cam['camera_T_world']);table=station['table'];z=table['center_world_m'][2]
    def project(points):
        points=np.asarray(points);pc=points@W[:3,:3].T+W[:3,3];uv=pc@K.T
        if np.any(pc[:,2]<=0):raise ValueError('Points behind camera')
        return uv[:,:2]/uv[:,2,None]
    output.mkdir(parents=True);bundle=output/'bundle';bundle.mkdir();shutil.copy2(source,output/'source_yam_bimanual_empty.xml');shutil.copy2(repo/'LICENSE',output/'ABC_LICENSE')
    (output/'calibration.json').write_text(json.dumps(station,ensure_ascii=False,indent=2))
    layouts=[]
    for i in range(6):
        boxes=[]
        for j in range(3):
            x=.44+.12*((i+j)%3);y=-.30+.30*j+.025*(i%2);a=np.deg2rad((-15,0,15)[i%3]);R=np.array([[np.cos(a),-np.sin(a)],[np.sin(a),np.cos(a)]])
            xy=np.array([[-.025,-.025],[.025,-.025],[.025,.025],[-.025,.025]])@R.T+[x,y]
            points=np.vstack([np.c_[xy,np.full(4,z)],np.c_[xy,np.full(4,z+.05)]])
            uv=project(points)
            assert np.all(uv>=[8,46]) and np.all(uv<=[631,471]),uv
            boxes.append(dict(material_id=f'TEST-CUBE-{j+1}',name=f'测试方块 {j+1}（5 cm）',quantity=1,size_m=[.05]*3,center_world_m=[x,y,z+.025],yaw_deg=float(np.rad2deg(a)),world_vertices_m=points.tolist(),image_vertices_px=uv.tolist()))
        ident=f'abc_layout_{i+1:02d}';layouts.append(dict(id=ident,objects=boxes,simulation_only=True,required_materials=[dict(material_id=o['material_id'],name=o['name'],quantity=1) for o in boxes]))
        image=Image.new('RGBA',(640,480));d=ImageDraw.Draw(image)
        d.rectangle((0,0,640,36),fill=(20,24,35,220));d.text((10,7),'ABC SCENE CALIBRATION | TEST ONLY | '+ident,fill=(255,220,100,255))
        # A tabletop metric grid checks scene/camera placement without object models.
        for axis in (0,1):
            start,end=(.35,.85) if axis==0 else (-.6,.6)
            for v in np.arange(start,end+.001,.1):
                points=[[v,-.6,z],[v,.6,z]] if axis==0 else [[.35,v,z],[.85,v,z]]
                d.line([tuple(p) for p in project(points)],fill=(150,200,240,85),width=1)
        edges=[(0,1),(1,2),(2,3),(3,0),(4,5),(5,6),(6,7),(7,4),(0,4),(1,5),(2,6),(3,7)]
        for j,box in enumerate(boxes):
            uv=np.array(box['image_vertices_px']);color=[(255,170,70,255),(100,240,160,255),(190,150,255,255)][j]
            for a,b in edges:d.line([tuple(uv[a]),tuple(uv[b])],fill=color,width=2)
            d.line([tuple(p) for p in uv[:4]]+[tuple(uv[0])],fill=color,width=4)
            d.text(tuple(uv.mean(axis=0)+[5,-15]),str(j+1)+' | 5 cm',fill='white')
        image.save(bundle/(ident+'.png'))
    manifest=dict(schema_version=2,task=dict(platform='yam',task_id='ABC-D405-PLACEMENT-TEST'),calibration_status='simulated',calibration_version=station['calibration_version'],camera=cam,simulation_only=True,layouts=layouts,source=station['source'],render_kind='analytic_geometry_projection',limitations=station['assumptions'])
    (bundle/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    # Independent planar mapping check: image -> ray -> intersection with z=table.
    errors=[];T=np.array(cam['world_T_camera'])
    for layout in layouts:
        for obj in layout['objects']:
            uv=np.array(obj['image_vertices_px'])[:4];rays=np.c_[uv,np.ones(4)]@np.linalg.inv(K).T@T[:3,:3].T
            dist=(z-T[2,3])/rays[:,2];xyz=T[:3,3]+rays*dist[:,None]
            errors.append(float(np.max(np.linalg.norm(xyz-np.array(obj['world_vertices_m'])[:4],axis=1))))
    assert max(errors)<1e-9
    report=dict(source_commit=sha,table_height_m=z,base_separation_m=float(np.linalg.norm(np.array(station['robot']['world_T_left_base'])[:3,3]-np.array(station['robot']['world_T_right_base'])[:3,3])),main_position_m=T[:3,3].tolist(),K=K.tolist(),max_ray_plane_roundtrip_error_m=max(errors),layouts=len(layouts),physics_tested=False,hardware_opened=False)
    (output/'validation.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source-repo',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--station-id',default='abc-simulated')
    for role in ('main','left','right'):
        p.add_argument('--'+role+'-serial',required=True,help='SDK serial from the local camera configuration')
    a=p.parse_args()
    build(a.source_repo,a.output,{role:getattr(a,role+'_serial') for role in ('main','left','right')},a.station_id)
