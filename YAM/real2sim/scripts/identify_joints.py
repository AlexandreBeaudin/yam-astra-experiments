"""Offline model convention identification on synthetic joint configurations."""
import sys,json,itertools
from pathlib import Path
import numpy as np
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation as R
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from real2sim.usd_robot import UsdRobot
from real2sim.xtrainer import nova2_fk
from real2sim.geometry import inverse,pose_vector
robot=UsdRobot(ROOT/'assets/upstream/robots/x_trainer.usd')
rng=np.random.default_rng(42);q=rng.uniform(-1.5,1.5,(16,6))
D=np.array([nova2_fk(x) for x in q]);out={}
for arm in (1,2):
 js=[next(j for j in robot.joints if j['name']==f'J{arm}_{i}') for i in range(1,7)]
 def model(qs):
  T=np.tile(np.eye(4),(len(qs),1,1))
  for i,j in enumerate(js):
   motion=np.tile(np.eye(4),(len(qs),1,1));motion[:,:3,:3]=R.from_rotvec(qs[:,i,None]*j['axis']).as_matrix()
   T=T@j['p0']@motion@inverse(j['p1'])
  return T
 def residual(v,sgn):
  offsets=np.r_[0,v[12:16],0]
  M=model(q*np.array(sgn)+offsets)
  pred=pose_vector(v[:6])@D@pose_vector(v[6:12])
  return np.c_[R.from_matrix(np.transpose(M[:,:3,:3],(0,2,1))@pred[:,:3,:3]).as_rotvec(),(M[:,:3,3]-pred[:,:3,3])*3].ravel()
 best=None
 for sgn in itertools.product((1,-1),repeat=6):
  fit=least_squares(residual,np.zeros(16),args=(sgn,),max_nfev=80,ftol=1e-7)
  if best is None or fit.cost<best[0]:
   best=(fit.cost,sgn,fit.x);print(arm,'best',fit.cost,sgn,fit.x[12:16].round(4),flush=True)
  if fit.cost<1e-5:break
 cost,sgn,v=best
 out[str(arm)]={'signs':list(sgn),'offsets_rad':np.r_[0,v[12:16],0].tolist(),'C_usd_base':pose_vector(v[:6]).tolist(),'F_flange_model_link':pose_vector(v[6:12]).tolist(),'cost':cost}
 (ROOT/'runs/joint_identification.json').write_text(json.dumps(out,indent=2))
