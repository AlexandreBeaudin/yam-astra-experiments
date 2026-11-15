"""Calibrated initial-layout candidates. Geometry projection, not physics rendering."""
import hashlib
import json
from pathlib import Path
import cv2
import numpy as np
from scipy.optimize import least_squares
from real2sim.camera import Camera
from real2sim.platforms.yam import YamKinematics


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class DrawerPlanner:
    def __init__(self, station, config, model):
        self.station, self.cfg = station, config
        if station['platform_id'] != 'yam' or station['calibration_status'] != 'verified':
            raise ValueError('Require accepted YAM calibration')
        spec = station['cameras']['main']
        if np.any(spec['distortion']):
            raise ValueError('This projector requires zero main-camera distortion; inverse distortion cannot be used as forward')
        self.cam = Camera(spec, spec['world_T_camera'])
        n = np.asarray(station['table']['normal_world'], float)
        n /= np.linalg.norm(n)
        x = np.array([1., 0, 0]); x -= n * np.dot(n, x); x /= np.linalg.norm(x)
        self.R = np.column_stack([x, np.cross(n, x), n])
        self.origin = np.array([0., 0., -station['table']['plane_d_m'] / n[2]])
        self.kin = YamKinematics(model)
        self.bases = {a: np.linalg.inv(np.array(station['robot']['world_T_'+a+'_base'])) for a in ['left', 'right']}

    def world(self, xyz):
        return np.asarray(xyz) @ self.R.T + self.origin

    @staticmethod
    def local(obj, padding=0.):
        sx, sy, sz = obj['size_m']; u,v = obj['uv_m']; angle=np.deg2rad(obj['yaw_deg'])
        R=np.array([[np.cos(angle),-np.sin(angle)],[np.sin(angle),np.cos(angle)]])
        footprint=np.array([[-sx/2-padding,-sy/2-padding],[sx/2+padding,-sy/2-padding],
                            [sx/2+padding,sy/2+padding],[-sx/2-padding,sy/2+padding]]) @ R.T + [u,v]
        return np.vstack([np.c_[footprint,np.zeros(4)],np.c_[footprint,np.full(4,sz)]])

    def propose(self, rng, bin_id, materials):
        yaw_bin, side_pattern = divmod(bin_id, 2)
        c=self.cfg; objects=[]
        for i,s in enumerate([c['objects'][0], *materials]):
            o={k:s[k] for k in ['material_id','name','size_m']}
            if i==0:
                o.update(uv_m=[rng.uniform(*c['drawer_center_u_m']),rng.uniform(*c['drawer_center_v_m'])],
                         yaw_deg=float(rng.uniform(*c['drawer_yaw_edges_deg'][yaw_bin:yaw_bin+2])))
            else:
                # Rotate which material is alone on one side, independently of yaw stratum.
                o.update(uv_m=[float(rng.uniform(.055,.30)),float(rng.uniform(-.745,-.64) if (i+side_pattern)%3 else rng.uniform(-.17,-.055))],
                         yaw_deg=float(rng.uniform(-80,80)))
            objects.append(o)
        if side_pattern:
            for o in objects[1:]: o['uv_m'][1]=-.8-o['uv_m'][1]
        return {'bin':bin_id,'objects':objects,'drawer_state':'closed','target_drawer':c['target_drawer'], 'required_materials':[{'material_id':o['material_id'],'name':o['name'],'quantity':1} for o in objects]}

    def corridor(self, drawer):
        o=dict(drawer);angle=np.deg2rad(o['yaw_deg']);d=self.cfg['pull_distance_m']
        o['uv_m']=(np.array(o['uv_m'])-np.array([np.cos(angle),np.sin(angle)])*(o['size_m'][0]/2+d/2)).tolist()
        o['size_m']=[d,o['size_m'][1],.0754]
        return o

    @staticmethod
    def overlap(a,b):
        return cv2.intersectConvexConvex(np.asarray(a,np.float32),np.asarray(b,np.float32))[0] > 1e-9

    def proxy_ik(self, point):
        results=[];lo,hi=self.kin.limits.T
        for arm, inv in self.bases.items():
            target=inv[:3,:3]@point+inv[:3,3]
            best=None
            for seed in [(lo+hi)/2,np.array([0.,1.7,1.6,0.,0.,0.])]:
                fit=least_squares(lambda q:self.kin.fk(q)[:3,3]-target,np.clip(seed,lo+1e-6,hi-1e-6),bounds=(lo,hi),max_nfev=90)
                error=float(np.linalg.norm(self.kin.fk(fit.x)[:3,3]-target))
                r={'arm':arm,'position_error_m':error,'q_rad':fit.x.tolist(),'passed':error<.002}
                if best is None or error<best['position_error_m']: best=r
                if r['passed']:break
            results.append(best)
        return {'world_flange_proxy_m':point.tolist(),'arms':results,'passed':any(r['passed'] for r in results)}

    def validate(self, layout, ik=True):
        c=self.cfg;obs=layout['objects'];bounds=np.asarray(c['provisional_workspace_uv_m'])
        pts=[self.local(o) for o in obs];pix=[self.cam.project(self.world(p)) for p in pts]
        checks={
          'within_provisional_workspace':all(np.all(p[:,:2]>=bounds[:,0]) and np.all(p[:,:2]<=bounds[:,1]) for p in pts),
          'main_camera_complete_view':all(np.all(z>0) and np.all(uv>=c['image_margin_px']) and np.all(uv<=np.array(self.cam.size)-1-c['image_margin_px']) for uv,z in pix),
          'object_separation':all(not self.overlap(self.local(obs[i],c['clearance_m']/2)[:4,:2],self.local(obs[j],c['clearance_m']/2)[:4,:2]) for i in range(len(obs)) for j in range(i)),
          'pull_corridor_clear':all(not self.overlap(self.local(self.corridor(obs[0]),c['clearance_m'])[:4,:2],p[:4,:2]) for p in pts[1:]),
          'projected_objects_separate':all(not self.overlap(cv2.convexHull(pix[i][0].astype(np.float32)),cv2.convexHull(pix[j][0].astype(np.float32))) for i in range(len(obs)) for j in range(i)),
        }
        corridor=self.local(self.corridor(obs[0]))
        checks['pull_corridor_in_provisional_workspace']=bool(np.all(corridor[:,:2]>=bounds[:,0]) and np.all(corridor[:,:2]<=bounds[:,1]))
        evidence=[]
        if all(checks.values()) and ik:
            for o in obs[1:]:
                evidence.append(self.proxy_ik(self.world([*o['uv_m'],o['size_m'][2]+c['flange_proxy_clearance_m']])))
            drawer=obs[0];a=np.deg2rad(drawer['yaw_deg']);front=np.array(drawer['uv_m'])-np.array([np.cos(a),np.sin(a)])*(drawer['size_m'][0]/2+c.get('drawer_handle_projection_m',.0198))
            for pull in [0.,c['pull_distance_m']]:
                uv=front-np.array([np.cos(a),np.sin(a)])*pull
                evidence.append(self.proxy_ik(self.world([*uv,.208+c['flange_proxy_clearance_m']])))
            checks['flange_position_proxy_ik']=all(r['passed'] for r in evidence)
        return {'passed':bool(all(checks.values()) and (not ik or checks.get('flange_position_proxy_ik',False))),
                'checks':{k:bool(v) for k,v in checks.items()},'flange_position_proxy_ik':evidence,
                'scope':'provisional geometric screening and position-only flange proxy; not grasp/path feasibility'}

    def draw(self, layout):
        img=np.zeros((self.cam.size[1],self.cam.size[0],4),np.uint8)
        for i,o in enumerate(layout['objects']):
            uv,_=self.cam.project(self.world(self.local(o)));uv=np.rint(uv).astype(int)
            color=[(40,190,255,255),(240,170,70,255),(240,240,240,255),(150,240,100,255)][i]
            for a,b in [(0,1),(1,2),(2,3),(3,0),(4,5),(5,6),(6,7),(7,4),(0,4),(1,5),(2,6),(3,7)]:
                cv2.line(img,tuple(uv[a]),tuple(uv[b]),color,2,cv2.LINE_AA)
            cv2.putText(img,o['material_id'],tuple(uv[4]+[0,-6]),cv2.FONT_HERSHEY_SIMPLEX,.38,color,1,cv2.LINE_AA)
        p,_=self.cam.project(self.world(self.local(self.corridor(layout['objects'][0]))[:4]));cv2.polylines(img,[p.astype(np.int32)],True,(100,150,255,180),1,cv2.LINE_AA)
        return img
