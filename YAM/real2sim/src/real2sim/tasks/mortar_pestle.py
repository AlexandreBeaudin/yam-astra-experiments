"""Initial-placement geometry only; no claimed deformable clay or handover success."""
import numpy as np
import cv2
from scipy.spatial.transform import Rotation


def prepare_meshes(path, scale):
    import trimesh
    parts=trimesh.load(path,force='mesh').split(only_watertight=False)
    # Identify the tall narrow pestle and broad mortar by geometry, not STL ordering.
    pestles=[m for m in parts if m.extents[2]>1.5*max(m.extents[:2])]
    if len(pestles)!=1:raise ValueError('Expected one tall narrow pestle; inspect STL')
    pestle=pestles[0];others=[m for m in parts if m is not pestle]
    if not others:raise ValueError('Missing mortar')
    bowl=trimesh.util.concatenate(others)
    result={}
    for name,m in [('bowl',bowl),('pestle',pestle)]:
        centre=m.bounds.mean(axis=0);centre[2]=m.bounds[0,2]
        m.apply_translation(-centre);m.apply_scale(scale);result[name]=m
    return result


def transform(layout, kind, meshes, spec):
    T=np.eye(4)
    if kind=='bowl':T[:3,3]=layout['bowl_m']
    else:
        yaw=layout['pestle_yaw_deg'];horizontal=spec['pestle_initial_orientation']=='horizontal'
        T[:3,:3]=Rotation.from_euler('z',yaw,degrees=True).as_matrix() @ Rotation.from_euler('y',90 if horizontal else 0,degrees=True).as_matrix()
        # Layout origin is footprint centre, at table height.
        v=meshes['pestle'].vertices@T[:3,:3].T
        anchor=np.r_[((v.max(axis=0)+v.min(axis=0))/2)[:2],v[:,2].min()]
        T[:3,3]=np.asarray(layout['pestle_m'])-anchor
    return T


def world_vertices(layout, kind, meshes, spec):
    T=transform(layout,kind,meshes,spec);return meshes[kind].vertices@T[:3,:3].T+T[:3,3]


def footprint_distance(layout, meshes, spec):
    # Conservative enclosing sphere/disk gap; includes the full rod length.
    bp=np.asarray(layout['bowl_m']);pp=np.asarray(layout['pestle_m'])
    br=max(meshes['bowl'].extents[:2])/2
    pr=np.linalg.norm(meshes['pestle'].extents[[0,2]])/2 if spec['pestle_initial_orientation']=='horizontal' else max(meshes['pestle'].extents[:2])/2
    return float(np.linalg.norm(bp[:2]-pp[:2])-br-pr)


def geometry_check(layout, meshes, spec, camera):
    checks={};points=np.vstack([world_vertices(layout,k,meshes,spec) for k in ('bowl','pestle')]);bounds=np.array(spec['table_bounds_xy'])
    checks['on_table']=bool(np.all(points[:,:2]>=bounds[:,0]+.01) and np.all(points[:,:2]<=bounds[:,1]-.01))
    uv,z=camera.project(points);w,h=camera.size;m=spec['image_margin_px']
    checks['complete_camera_frustum']=bool(np.all(z>.005) and np.all(uv>=m) and np.all(uv<=[w-1-m,h-1-m]))
    checks['object_clearance']=footprint_distance(layout,meshes,spec)>=spec['object_clearance_m']
    return checks


def waypoint_checks(layout,meshes,spec,reach):
    # These positions use the existing estimated gripper offset. Orientation is
    # fixed downward; do not call this a verified pestle grasp or transfer.
    p=np.array(layout['pestle_m']);b=np.array(layout['bowl_m']);height=meshes['pestle'].extents[0]/2 if spec['pestle_initial_orientation']=='horizontal' else meshes['pestle'].extents[2]*.65
    grasp=p+[0,0,height+spec['palm_to_grasp_proxy_m']]
    bowl_above=b+[0,0,meshes['bowl'].extents[2]+.06+spec['palm_to_grasp_proxy_m']]
    targets=[(1,'left_grasp_workspace',grasp),(1,'left_approach_workspace',grasp+[0,0,.08]),(1,'left_lift_workspace',grasp+[0,0,.10]),(2,'right_above_bowl_workspace',bowl_above),(2,'right_retracted_workspace',bowl_above+[0,0,.06])]
    records=[];previous={1:None,2:None}
    for arm,name,target in targets:
        check=reach.solve(arm,target,previous[arm]);records.append(dict(arm=arm,name=name,palm_position_m=target.tolist(),**check))
        if not check['passed']:break
        previous[arm]=check['q_rad']
    return records


def draw(layout, meshes, spec, camera):
    rgba=np.zeros((camera.size[1],camera.size[0],4),np.uint8)
    for name,color in [('bowl',(100,220,255,210)),('pestle',(255,170,100,230))]:
        v=world_vertices(layout,name,meshes,spec);uv,_=camera.project(v);hull=cv2.convexHull(np.rint(uv).astype(np.int32));cv2.polylines(rgba,[hull],True,color,2,cv2.LINE_AA)
        centre=v.mean(axis=0);q,_=camera.project([centre]);cv2.putText(rgba,name.upper(),tuple(q[0].astype(int)),cv2.FONT_HERSHEY_SIMPLEX,.4,color,1,cv2.LINE_AA)
    b=np.array(layout['bowl_m']);angles=np.linspace(0,2*np.pi,64);clay=b+[0,0,spec['clay_center_height_in_bowl_m']];ring=clay+spec['clay_radius_m']*np.c_[np.cos(angles),np.sin(angles),np.zeros_like(angles)];uv,_=camera.project(ring);cv2.polylines(rgba,[uv.astype(np.int32)],True,(200,100,255,220),2)
    return rgba
