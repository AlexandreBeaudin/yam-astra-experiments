"""Train-only main-camera reprojection refinement with fixed intrinsics and separate board mounts."""
import sys,json,argparse
from pathlib import Path
import numpy as np
from scipy.spatial.transform import Rotation
from scipy.optimize import least_squares

def refine(source,intrinsics,output):
 if output.exists():raise ValueError("Choose a new output file; existing results are immutable")
 d=json.loads(source.read_text());cam=next(x for x in json.loads(intrinsics.read_text())['cameras'] if x['serial']=='408122072021' and x['format']=='format.rgb8');assert not any(cam['coeffs'])
 K=np.array([[cam['fx'],0,cam['ppx']],[0,cam['fy'],cam['ppy']],[0,0,1.]])
 def unpack(p):
  t=np.eye(4);t[:3,:3]=Rotation.from_rotvec(p[:3]).as_matrix();t[:3,3]=p[3:];return t
 def pack(t):return np.r_[Rotation.from_matrix(t[:3,:3]).as_rotvec(),t[:3,3]]
 # Charuco ids index row-major inner corners of confirmed 15 x 8 board.
 def obj(row):
  ids=np.array(row['corner_ids']);return np.c_[(ids%14+1)*.019,(ids//14+1)*.019,np.zeros(len(ids))]
 def prediction(p,row):
  X,Y=unpack(p[:6]),unpack(p[6:]);B=np.linalg.inv(X)@np.array(row['base_T_link6'])@Y;xyz=obj(row)@B[:3,:3].T+B[:3,3];pix=xyz@K.T;return pix[:,:2]/pix[:,2:3],B
 train=[r for r in d['observations'] if r['split']=='train']
 p0=np.r_[pack(np.array(d['transforms']['base_T_camera'])),pack(np.array(d['transforms']['link6_T_board']))]
 def residual(p):return np.concatenate([(prediction(p,r)[0]-r['image_points']).ravel() for r in train])
 fit=least_squares(residual,p0,loss='soft_l1',f_scale=1.,x_scale='jac',max_nfev=200)
 out={'status':'diagnostic_candidate_not_accepted','method':'fixed factory main intrinsics; robust soft_l1 1px reprojection, training corners only; no joint offsets or per-frame free board pose; original holdout reused for evaluation','source':str(source),'optimizer_success':bool(fit.success),'transforms':{'base_T_camera':unpack(fit.x[:6]).tolist(),'link6_T_board':unpack(fit.x[6:]).tolist()},'observations':[]}
 for r in d['observations']:
  pred,B=prediction(fit.x,r);E=np.linalg.inv(np.array(r['camera_T_board']))@B;out['observations'].append({**r,'reprojection_rmse_px':float(np.sqrt(np.mean(np.sum((pred-r['image_points'])**2,axis=1)))),'translation_error_mm':float(np.linalg.norm(E[:3,3])*1000),'rotation_error_deg':float(np.linalg.norm(Rotation.from_matrix(E[:3,:3]).as_rotvec())*180/np.pi)})
 for split in ['train','validation']:
  rows=[r for r in out['observations'] if r['split']==split];out[split]={'n':len(rows),**{key:float(np.sqrt(np.mean([r[key]**2 for r in rows]))) for key in ['reprojection_rmse_px','translation_error_mm','rotation_error_deg']},'max_reprojection_rmse_px':max(r['reprojection_rmse_px'] for r in rows)}
 output.write_text(json.dumps(out,indent=2));print(source.parent,source.name,out['train'],out['validation'],flush=True)
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--intrinsics',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();refine(a.source,a.intrinsics,a.output)
