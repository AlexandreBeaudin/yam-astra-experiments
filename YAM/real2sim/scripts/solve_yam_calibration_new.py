"""Solve fixed-main and wrist candidates from frozen frame groups, evaluate holdouts."""
import sys,json,argparse
from pathlib import Path
import numpy as np
from scipy.spatial.transform import Rotation

def solve(root,workbench,yam):
 sys.path.insert(0,str(workbench/'src'));sys.path.append(str(yam/'gello_software/.venv/lib/python3.11/site-packages'))
 import cv2,pyrealsense2 as rs
 from real2sim.calibration.board import BoardSpec,make_board
 from real2sim.calibration.handeye import solve_eye_to_hand
 from real2sim.platforms.yam import YamKinematics
 intr=json.loads((root/'device_intrinsics.json').read_text());spec=BoardSpec(squares_x=15,squares_y=8,square_length_m=.019,marker_length_m=.014,legacy_pattern=True);board=make_board(spec);objall=board.getChessboardCorners()
 kin=YamKinematics(yam/'i2rt/i2rt/robot_models/arm/yam/yam.xml')
 output=root/'solve_v1';output.mkdir(exist_ok=False);summary={'status':'candidate_not_station_verified','intrinsics_source':'current same-serial same-resolution factory profiles; not embedded at acquisition','board':spec.to_dict(),'tasks':{}}
 poses={};models={}
 for folder in sorted((root/'selection_v1').glob('calib*')):
  meta=json.loads((folder/'selection.json').read_text());camera=meta['camera'];serial=meta['session']['cameras'][camera]['serial'];d=next(x for x in intr['cameras'] if x['serial']==serial and x['format']=='format.rgb8')
  assert meta['session']['cameras'][camera]['rotation']==0
  k=rs.intrinsics()
  for key in ['width','height','fx','fy','ppx','ppy','coeffs']:setattr(k,key,d[key])
  k.model=rs.distortion.inverse_brown_conrady
  def project(obj,T):
   xyz=np.asarray(obj)@T[:3,:3].T+T[:3,3]
   return np.array([rs.rs2_project_point_to_pixel(k,v.tolist()) for v in xyz])
  rows=[]
  for r in meta['selected']:
   obj=objall[np.array(r['corner_ids'])].astype(np.float64);image=np.array(r['image_points'],np.float64)
   norm=np.array([rs.rs2_deproject_pixel_to_point(k,p.tolist(),1)[:2] for p in image],np.float64)
   ok,rv,tv=cv2.solvePnP(obj,norm,np.eye(3),np.zeros(5),flags=cv2.SOLVEPNP_ITERATIVE)
   if not ok:raise ValueError('PnP failed')
   B=np.eye(4);B[:3,:3]=cv2.Rodrigues(rv)[0];B[:3,3]=tv.ravel()
   pred=project(obj,B);rmse=float(np.sqrt(np.mean(np.sum((pred-image)**2,axis=1))))
   row={**r,'camera_T_board':B.tolist(),'base_T_link6':kin.fk(r['joint_position']).tolist(),'pnp_rmse_px':rmse,'positive_depth':bool(np.all((obj@B[:3,:3].T+B[:3,3])[:,2]>0))}
   rows.append(row)
  poses[folder.name]=rows
  if 'table' in folder.name:
   (output/(folder.name+'_observations.json')).write_text(json.dumps(rows,indent=2));continue
  # All preselected holds remain in their frozen split. No validation-driven outlier removal.
  train=[r for r in rows if r['split']=='train'];valid=[r for r in rows if r['split']=='validation']
  if len(train)<3:
   summary['tasks'][folder.name]={'status':'insufficient_training_groups'};continue
  wrist='_wrist_' in folder.name
  A=[np.array(r['base_T_link6']) for r in train];B=[np.array(r['camera_T_board']) for r in train]
  result=solve_eye_to_hand(A,[np.linalg.inv(b) for b in B] if wrist else B)
  X=result.X_base_cam;Y=result.Y_link_target
  evaluated=[]
  for r in rows:
   a=np.array(r['base_T_link6']);b=np.array(r['camera_T_board']);bpred=np.linalg.inv(Y)@np.linalg.inv(a)@X if wrist else np.linalg.inv(X)@a@Y
   err=np.linalg.inv(b)@bpred;pred=project(objall[np.array(r['corner_ids'])],bpred);pix=np.array(r['image_points']);e=dict(r,translation_error_mm=float(np.linalg.norm(err[:3,3])*1000),rotation_error_deg=float(np.linalg.norm(Rotation.from_matrix(err[:3,:3]).as_rotvec())*180/np.pi),reprojection_rmse_px=float(np.sqrt(np.mean(np.sum((pred-pix)**2,axis=1)))))
   evaluated.append(e)
   im=cv2.imread(str(root/'selection_v1'/r['image']))
   for measured,predicted in zip(pix,pred):
    if np.isfinite(predicted).all() and np.max(abs(predicted))<10000:
     u=tuple(np.round(measured).astype(int));v=tuple(np.round(predicted).astype(int));cv2.circle(im,u,2,(0,255,0),-1);cv2.circle(im,v,2,(0,0,255),1);cv2.line(im,u,v,(0,180,255),1)
   cv2.putText(im,f"{r['split']} {e['reprojection_rmse_px']:.2f}px",(10,25),cv2.FONT_HERSHEY_SIMPLEX,.6,(0,0,255),2)
   dest=output/folder.name/r['split'];dest.mkdir(parents=True,exist_ok=True);cv2.imwrite(str(dest/(Path(r['image']).stem+'_reprojection.png')),im)
  def stats(split):
   rsx=[r for r in evaluated if r['split']==split]
   return {'n':len(rsx),**{key:{'rmse':float(np.sqrt(np.mean([r[key]**2 for r in rsx]))),'max':float(max(r[key] for r in rsx))} for key in ['translation_error_mm','rotation_error_deg','reprojection_rmse_px','pnp_rmse_px']}} if rsx else {'n':0}
  item={'kind':'eye_in_hand' if wrist else 'eye_to_hand','train':stats('train'),'validation':stats('validation'),'axis_singular_values':result.axis_singular_values.tolist(),'transforms':{'base_T_board':X.tolist(),'link6_T_camera':Y.tolist()} if wrist else {'base_T_camera':X.tolist(),'link6_T_board':Y.tolist()},'observations':evaluated,'status':'candidate_pending_review'}
  (output/(folder.name+'.json')).write_text(json.dumps(item,indent=2));summary['tasks'][folder.name]={k:v for k,v in item.items() if k!='observations'};models[folder.name]=(X,Y)
  np.savez_compressed(output/(folder.name+'_train.npz'),base_T_tool=A,camera_T_target=B)
  print(folder.name,json.dumps({'train':item['train'],'validation':item['validation']}),flush=True)
 if all(t in models for t in ['calib_left_main_new','calib_right_main_new']):
  left=models['calib_left_main_new'][0];right=models['calib_right_main_new'][0];summary['left_base_T_right_base']=(left@np.linalg.inv(right)).tolist()
  table=[]
  for r in poses.get('calib_table_check_new',[]):
   W=left@np.array(r['camera_T_board']);normal=W[:3,2]
   if normal[2]<0:normal=-normal
   table.append({'time_s':r['time_s'],'split':r['split'],'normal_in_left_base':normal.tolist(),'point_in_left_base_m':W[:3,3].tolist(),'plane_d_m':float(-normal@W[:3,3]),'pnp_rmse_px':r['pnp_rmse_px']})
  summary['table_board_surface_planes']=table
  summary['table_status']='board_surface_only; backing thickness unknown; hands may touch board; main extrinsics must pass first'
 (output/'summary.json').write_text(json.dumps(summary,indent=2))
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--workbench',type=Path,required=True);p.add_argument('--yam',type=Path,required=True);a=p.parse_args();solve(a.root,a.workbench,a.yam)
