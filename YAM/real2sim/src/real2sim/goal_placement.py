"""Goal-scoring placement adapter: metric geometry, calibrated view, waypoint IK.

Does not establish collision-free motion, image identity, or scoring success.
"""
import math
import numpy as np
import cv2
from .camera import Camera


def axes(yaw):
    a=math.radians(yaw)
    return np.array([math.cos(a),math.sin(a),0.]),np.array([-math.sin(a),math.cos(a),0.])


def geometry(layout, spec):
    g=np.array(layout['goal_m']);b=np.array(layout['ball_m']);n,t=axes(layout['goal_yaw_deg'])
    w,h,d,dt=spec['goal_width_m'],spec['goal_height_m'],spec['goal_depth_m'],spec['goal_top_depth_m']
    # n points into goal; origin is midpoint between front post centres at table plane.
    v=np.array([g+t*s*(w-spec['frame_m'])/2+n*depth+[0,0,z] for z,depth in [(0,0),(h-spec['frame_m']/2,0),(0,d-spec['frame_m']/2),(h-spec['frame_m']/2,dt)] for s in [-1,1]])
    a=np.linspace(0,2*np.pi,32,endpoint=False)
    sphere=np.array([b+spec['ball_radius_m']*np.array([np.cos(u)*np.cos(vv),np.sin(u)*np.cos(vv),np.sin(vv)]) for u in a for vv in np.linspace(-np.pi/2,np.pi/2,17)])
    return v,sphere


def features(layout):
    n,t=axes(layout['goal_yaw_deg']);delta=np.array(layout['goal_m'])-layout['ball_m'];delta[2]=0
    return float(np.linalg.norm(delta)),math.degrees(math.atan2(np.dot(delta,t),np.dot(delta,n)))


def bin_index(layout, spec):
    distance,angle=features(layout)
    indices=[]
    for value,edges in [(distance,spec['distance_edges_m']),(angle,spec['angle_edges_deg'])]:
        if not edges[0]<=value<=edges[-1]:return None
        indices.append(min(len(edges)-2,int(np.searchsorted(edges,value,side='right')-1)))
    return indices[0]*3+indices[1]


def validate(layout,spec,camera,reach=None):
    result={'passed':False,'checks':{},'waypoint_ik':[]}
    try:
        g=np.asarray(layout['goal_m'],float);b=np.asarray(layout['ball_m'],float);yaw=float(layout['goal_yaw_deg']);arm=layout['arm']
        if g.shape!=(3,) or b.shape!=(3,) or not np.isfinite(np.r_[g,b,yaw]).all() or arm not in (1,2):raise ValueError()
    except (ValueError,KeyError,TypeError):return dict(result,reasons=['invalid_pose'])
    verts,sphere=geometry(layout,spec);n,t=axes(yaw)
    # Expand centreline cage by frame radius, so complete outer silhouette is checked.
    cage=np.vstack([verts+[x,y,z] for x in [-spec['frame_m']/2,spec['frame_m']/2] for y in [-spec['frame_m']/2,spec['frame_m']/2] for z in [-spec['frame_m']/2,spec['frame_m']/2]])
    points=np.vstack([cage,sphere]);bounds=np.array(spec['table_bounds_xy'])
    c=result['checks'];c['on_table']=bool(np.all(points[:,:2]>=bounds[:,0]+.01) and np.all(points[:,:2]<=bounds[:,1]-.01) and abs(g[2]-spec['table_z'])<.001 and abs(b[2]-spec['table_z']-spec['ball_radius_m'])<.001)
    uv,z=camera.project(points);w,h=camera.size;m=spec['image_margin_px']
    c['complete_camera_frustum']=bool(np.all(z>0) and np.all(uv>=m) and np.all(uv<=[w-1-m,h-1-m]))
    distance,angle=features(layout)
    c['distribution_range']=bin_index(layout,spec) is not None
    c['ball_outside_goal']=bool(np.dot(g-b,n)>.07)
    c['opening_fits_ball']=spec['goal_width_m']-2*spec['frame_m']>2*spec['ball_radius_m']+.01
    # The ray to the goal centre clears both front posts at the ball's height.
    c['entry_clearance']=bool((spec['goal_width_m']/2-spec['frame_m'])*math.cos(math.radians(angle))>spec['ball_radius_m']+.005)
    direction=g-b;direction[2]=0;direction/=max(np.linalg.norm(direction),1e-9)
    positions=[b+direction*s+[0,0,spec['palm_to_contact_m']+lift] for s,lift in [(-.065,.07),(-.065,0),(0,0),(.025,0)]]
    c['strike_corridor_on_table']=bool(all(np.all(p[:2]-.035>=bounds[:,0]) and np.all(p[:2]+.035<=bounds[:,1]) for p in positions))
    if all(c.values()) and reach is not None:
        previous=None
        for name,p in zip(['approach','pre_strike','contact','follow_through'],positions):
            check=reach.solve(arm,p,previous);result['waypoint_ik'].append(dict(name=name,palm_position_m=p.tolist(),**check))
            if not check['passed']:break
            previous=check['q_rad']
        c['waypoint_ik']=len(result['waypoint_ik'])==4 and all(x['passed'] for x in result['waypoint_ik'])
    result['reasons']=[k for k,v in c.items() if not v]
    result['passed']=all(c.values()) and c.get('waypoint_ik',False)
    result['scope']='geometric placement and fixed downward-palm IK only; no swept collisions, robot occlusion, rolling or scoring validation'
    return result


def propose(rng,spec,bin_id,arm):
    di,ai=divmod(bin_id,3);d=rng.uniform(*spec['distance_edges_m'][di:di+2]);a=rng.uniform(*spec['angle_edges_deg'][ai:ai+2])
    yaw=rng.uniform(*spec.get('left_yaw_range_deg',[-125,-55])) if arm==1 else rng.uniform(*spec.get('right_yaw_range_deg',[55,125]))
    n,t=axes(yaw);g=np.array([rng.uniform(*spec.get('goal_x_range_m',[-.30,-.12])),rng.uniform(*spec.get('goal_y_range_m',[-.60,-.49])),spec['table_z']])
    b=g-d*(n*np.cos(np.deg2rad(a))+t*np.sin(np.deg2rad(a)));b[2]+=spec['ball_radius_m']
    return dict(goal_m=g.tolist(),ball_m=b.tolist(),goal_yaw_deg=float(yaw),arm=arm)


def landmarks(layout,spec):
    g=np.array(layout['goal_m']);n,t=axes(layout['goal_yaw_deg']);half=(spec['goal_width_m']-spec['frame_m'])/2
    return np.array([layout['ball_m'],g-t*half,g+t*half])


def infer(pixels,spec,camera,arm):
    p=np.asarray(pixels,float)
    if p.shape!=(3,2) or not np.isfinite(p).all():raise ValueError('依次标注球心、球门 A 柱脚中心、B 柱脚中心')
    w,h=camera.size
    if np.any(p<0) or np.any(p>[w-1,h-1]):raise ValueError('标记超出画面')
    b=camera.backproject(p[:1],spec['table_z']+spec['ball_radius_m'])[0]
    posts=camera.backproject(p[1:],spec['table_z']);span=np.linalg.norm(posts[1]-posts[0]);expected=spec['goal_width_m']-spec['frame_m']
    if abs(span-expected)>.015:raise ValueError('柱脚间距不符：请检查标注、尺寸与标定')
    g=posts.mean(axis=0);t=(posts[1]-posts[0])/span;n=np.array([t[1],-t[0],0.]);yaw=np.degrees(np.arctan2(n[1],n[0]))
    return dict(ball_m=b.tolist(),goal_m=g.tolist(),goal_yaw_deg=float(yaw),arm=arm)


def draw(layout,spec,camera):
    rgba=np.zeros((camera.size[1],camera.size[0],4),np.uint8);v,s=geometry(layout,spec);uv,_=camera.project(v);uv=np.rint(uv).astype(int)
    for i,j in [(0,2),(1,3),(2,3),(0,4),(1,5),(4,6),(5,7),(6,7),(2,6),(3,7),(4,5)]:cv2.line(rgba,tuple(uv[i]),tuple(uv[j]),(90,220,255,255),3,cv2.LINE_AA)
    for f in np.linspace(0,1,9):
        for start,end in [(v[4]*(1-f)+v[5]*f,v[6]*(1-f)+v[7]*f),(v[6]*(1-f)+v[7]*f,v[2]*(1-f)+v[3]*f)]:
            p,_=camera.project([start,end]);cv2.line(rgba,tuple(p[0].astype(int)),tuple(p[1].astype(int)),(100,180,210,180),1)
    sp,_=camera.project(s);cv2.fillConvexPoly(rgba,cv2.convexHull(sp.astype(np.int32)),(240,240,240,255));cv2.polylines(rgba,[cv2.convexHull(sp.astype(np.int32))],True,(50,50,50,255),1)
    marks,_=camera.project(landmarks(layout,spec));g,_=camera.project([layout['goal_m']]);cv2.arrowedLine(rgba,tuple(marks[0].astype(int)),tuple(g[0].astype(int)),(140,255,120,255),1,tipLength=.08)
    for label,p in zip(['BALL','A','B'],marks):cv2.putText(rgba,label,tuple(p.astype(int)+[5,-5]),cv2.FONT_HERSHEY_SIMPLEX,.4,(255,255,255,255),1,cv2.LINE_AA)
    return rgba
