"""Offline two-arm guards. No drivers, sockets, calibration inference or IK."""
from __future__ import annotations
import json
import hashlib
from pathlib import Path
import numpy as np

ARM_INDICES = np.r_[0:6,7:13]


def finite(value, shape):
    value=np.asarray(value,dtype=float)
    if value.shape!=shape or not np.all(np.isfinite(value)):
        raise ValueError(f'expected finite geometry of shape {shape}')
    return value


class HoldOnlyExclusion:
    mode='unregistered_joint_space_hold_only'
    ready=False
    def describe(self):
        return dict(mode=self.mode, configuration_sha256=getattr(self,'configuration_sha256',None), motion_ready=False, metric_separation_verified=False,
            reason='No registered base transforms or reviewed joint-space envelope. Yaw, reach and all other arm joints are excluded from movement.')
    def reset(self, full):self.initial=finite(full,(14,)).copy()
    def check_path(self, path):
        path=np.asarray(path,dtype=float)
        if path.ndim!=2 or path.shape[1]!=14 or not len(path) or not np.all(np.isfinite(path)):return 'invalid separation path'
        if np.any(np.abs(path[:,ARM_INDICES]-self.initial[ARM_INDICES])>1e-9):
            return 'joint-space exclusion: observer mode is hold-only until geometry or a reviewed envelope is supplied'
        return None


class JointEnvelope:
    mode='reviewed_joint_space_exclusion'
    ready=True
    def __init__(self, config):
        if config.get('reviewed') is not True or not str(config.get('provenance','')).strip():
            raise ValueError('joint-space fallback requires reviewed=true and provenance')
        self.boxes={side:finite(config[f'{side}_joint_box'],(6,2)) for side in ['left','right']}
        if any(np.any(box[:,0]>=box[:,1]) for box in self.boxes.values()):
            raise ValueError('joint-space bounds must have positive width')
    def reset(self, full):pass
    def describe(self):return dict(mode=self.mode,configuration_sha256=getattr(self,'configuration_sha256',None),motion_ready=True,metric_separation_verified=False,
        reason='Reviewed joint boxes constrain yaw/reach and all six joints on both arms. This is not a measured 0.14 m FK certificate.')
    def check_path(self,path):
        path=np.asarray(path,dtype=float)
        if path.ndim!=2 or path.shape[1]!=14 or not len(path) or not np.all(np.isfinite(path)):return 'invalid separation path'
        for side,indices in [('left',range(6)),('right',range(7,13))]:
            q=np.asarray(path)[:,list(indices)];box=self.boxes[side]
            if np.any(q<box[:,0]) or np.any(q>box[:,1]):return f'{side} yaw/reach joint-space exclusion envelope violated'
        return None


class WristFK:
    def __init__(self):
        from planned_motion import PosePlanner
        self.planner=PosePlanner()
        mj=self.planner.mj
        self.wrist=mj.mj_name2id(self.planner.model,mj.mjtObj.mjOBJ_SITE,'tcp_site')
        if self.wrist<0:raise ValueError('model lacks wrist/tcp_site')
        # A global arm-chain length bound, used to cover motion between checked samples.
        model=self.planner.model
        body=int(model.site_bodyid[self.planner.site]);length=float(np.linalg.norm(model.site_pos[self.planner.site]))
        while body:
            length+=float(np.linalg.norm(model.body_pos[body]));body=int(model.body_parentid[body])
        self.reach_bound=length
    def points(self,q):
        grasp,_=self.planner.fk(q)
        return np.vstack([self.planner.data.site_xpos[self.wrist].copy(),grasp])


class RegisteredSeparation:
    mode='registered_fk_wrist_gripper'
    ready=True
    MAX_SUBDIVISION_DEPTH=10
    MAX_EXTRA_SAMPLES=128
    def __init__(self,config,*,fk=None,minimum_allowed=.14):
        if config.get('verified') is not True or not str(config.get('provenance','')).strip():
            raise ValueError('base transforms require verified=true and measurement provenance')
        self.transforms={}
        for side in ['left','right']:
            t=finite(config[f'{side}_base_to_station'],(4,4))
            if (not np.allclose(t[3],[0,0,0,1],atol=1e-8)
                    or not np.allclose(t[:3,:3].T@t[:3,:3],np.eye(3),atol=1e-6)
                    or not np.isclose(np.linalg.det(t[:3,:3]),1,atol=1e-6)):
                raise ValueError('base transforms must be proper rigid transforms in metres')
            self.transforms[side]=t
        self.minimum=float(config.get('minimum_separation_m',.14))
        self.margin=float(config.get('margin_m',.02))
        if not np.isfinite(self.minimum) or self.minimum<minimum_allowed or not np.isfinite(self.margin) or self.margin<0:
            raise ValueError(f'minimum separation must be >={minimum_allowed:g} m; margin must be nonnegative')
        self.fk=fk or WristFK()
    def reset(self,full):pass
    def describe(self):return dict(mode=self.mode,configuration_sha256=getattr(self,'configuration_sha256',None),motion_ready=True,metric_separation_verified=True,
        minimum_separation_m=self.minimum,margin_m=self.margin,
        certificate_method='adaptive_two_endpoint_lipschitz_v1',
        coverage='Model wrist/tcp and grasp-site points in registered station frame; not a whole-arm/table/cable collision certificate.')
    def distance(self,full):
        points=[]
        for side,q in [('left',full[:7]),('right',full[7:])]:
            t=self.transforms[side];local=self.fk.points(q)
            points.append(local@t[:3,:3].T+t[:3,3])
        return float(np.min(np.linalg.norm(points[0][:,None,:]-points[1][None,:,:],axis=-1)))
    def check_path(self,path):
        path=np.asarray(path,dtype=float)
        threshold=self.minimum+self.margin
        self.last_check=dict(certificate_method='adaptive_two_endpoint_lipschitz_v1',
            threshold_m=threshold,evaluated_poses=0,extra_samples=0,certified_intervals=0,
            min_sampled_distance_m=None,min_certified_lower_bound_m=None)
        def finish(reason=None):
            self.last_check.update(accepted=reason is None,reason=reason)
            return reason
        if path.ndim!=2 or path.shape[1]!=14 or not len(path) or not np.all(np.isfinite(path)):
            return finish('invalid separation path')
        if not np.isfinite(self.fk.reach_bound) or self.fk.reach_bound<=0:
            return finish('inter-arm model reach bound is unavailable')
        def measure(q):
            value=float(self.distance(q))
            self.last_check['evaluated_poses']+=1
            if np.isfinite(value):
                current=self.last_check['min_sampled_distance_m']
                self.last_check['min_sampled_distance_m']=value if current is None else min(current,value)
            return value
        distances=[measure(q) for q in path]
        if not np.all(np.isfinite(distances)):
            return finish('inter-arm model separation is unavailable')
        if min(distances)<threshold:
            return finish('inter-arm wrist/gripper separation below configured threshold')
        for index,(a,b,da,db) in enumerate(zip(path,path[1:],distances,distances[1:])):
            pending=[(a,b,da,db,0)]
            while pending:
                a,b,da,db,depth=pending.pop()
                excursion=self.fk.reach_bound*float(np.abs(b[ARM_INDICES]-a[ARM_INDICES]).sum())
                # At fraction t, d(t)>=da-t*excursion AND
                # d(t)>=db-(1-t)*excursion. Their maximum is at least
                # (da+db-excursion)/2 everywhere. This covers the entire
                # interval, unlike checking FK samples alone. Cap at the
                # endpoint minimum to remain conservative under roundoff.
                lower=min(da,db,(da+db-excursion)/2)
                if lower>=threshold:
                    self.last_check['certified_intervals']+=1
                    current=self.last_check['min_certified_lower_bound_m']
                    self.last_check['min_certified_lower_bound_m']=lower if current is None else min(current,lower)
                    continue
                if depth>=self.MAX_SUBDIVISION_DEPTH or self.last_check['extra_samples']>=self.MAX_EXTRA_SAMPLES:
                    self.last_check.update(blocked_path_interval=index,blocked_depth=depth,
                        blocked_lower_bound_m=lower,blocked_motion_bound_m=excursion)
                    return finish('inter-arm interpolated separation cannot be certified')
                middle=(a+b)/2
                dm=measure(middle)
                self.last_check['extra_samples']+=1
                if not np.isfinite(dm):
                    return finish('inter-arm model separation is unavailable')
                if dm<threshold:
                    self.last_check.update(blocked_path_interval=index,blocked_depth=depth+1)
                    return finish('inter-arm wrist/gripper separation below configured threshold')
                pending.append((middle,b,dm,db,depth+1))
                pending.append((a,middle,da,dm,depth+1))
        return finish()
        return None


def load_guard(path=None, *, minimum_allowed=.14):
    if path is None:return HoldOnlyExclusion()
    raw=Path(path).read_bytes()
    config=json.loads(raw)
    if config.get('schema_version')!=1:raise ValueError('observer geometry schema_version must be 1')
    if config.get('mode')=='registered_fk':
        guard=HoldOnlyExclusion() if config.get('verified') is False else RegisteredSeparation(config, minimum_allowed=minimum_allowed)
    elif config.get('mode')=='joint_space_exclusion':guard=JointEnvelope(config)
    else:raise ValueError('unknown observer geometry mode')
    guard.configuration_sha256=hashlib.sha256(raw).hexdigest()
    return guard
