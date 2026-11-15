"""Fit tabletop candidate from spatially grouped board placements; backing height is approximate."""
import argparse,json,shutil,sys
from pathlib import Path
import numpy as np

def fit(root,workbench,thickness):
 sys.path.insert(0,str(workbench/'src'))
 from real2sim.calibration.board import BoardSpec,make_board
 out=root/'solve_v1';summary=json.loads((out/'summary.json').read_text());rows=json.loads((out/'calib_table_check_new_observations.json').read_text());X=np.array(summary['tasks']['calib_left_main_new']['transforms']['base_T_camera']);obj=make_board(BoardSpec(squares_x=15,squares_y=8,square_length_m=.019,marker_length_m=.014)).getChessboardCorners();center=obj.mean(axis=0)
 groups=[]
 for r in rows:
  B=np.array(r['camera_T_board']);c=B[:3,:3]@center+B[:3,3];index=next((i for i,g in enumerate(groups) if np.linalg.norm(c-g['center'])<.03),None)
  if index is None:groups.append({'center':c,'rows':[r]})
  else:groups[index]['rows'].append(r)
 selected=[]
 for i,g in enumerate(groups):
  r=min(g['rows'],key=lambda x:x['pnp_rmse_px']);T=X@np.array(r['camera_T_board']);points=obj@T[:3,:3].T+T[:3,3];split='validation' if i%4==3 else 'train';dest=out/'table_grouped'/split;dest.mkdir(parents=True,exist_ok=True);image=dest/Path(r['image']).name;shutil.copyfile(root/'selection_v1'/r['image'],image)
  selected.append({'group_id':i,'time_s':r['time_s'],'all_group_times_s':[z['time_s'] for z in g['rows']],'split':split,'points':points,'source_image':r['image'],'grouped_image':str(image.relative_to(root))})
 train=np.concatenate([r['points'] for r in selected if r['split']=='train']);mu=train.mean(axis=0);_,_,vh=np.linalg.svd(train-mu,full_matrices=False);n=vh[-1]
 if n[2]<0:n=-n
 d=-n@mu
 for r in selected:
  e=r.pop('points')@n+d;r['plane_distance_rmse_mm']=float(np.sqrt(np.mean(e**2))*1000);r['plane_distance_max_mm']=float(max(abs(e))*1000)
 result={'status':'candidate_not_station_verified','normal_in_left_base':n.tolist(),'board_surface_d_m':float(d),'physical_table_d_m':float(d+thickness),'equation':'normal dot point + d = 0','backing_thickness_m':thickness,'thickness_source':'user reports approximately 35 mm; uncertainty unmeasured; offset along fitted normal','physical_table_z_at_left_base_origin_m':float(-(d+thickness)/n[2]),'grouping':'3 cm board-center clusters in camera frame; split before plane fitting; one frame per cluster; authoritative TABLE split overrides preliminary time-only selection','groups':selected,'validation_note':'board-plane consistency against held-out placements; not absolute metrology; hand contact visible; main extrinsics uncertainty remains'}
 (out/'table_plane_candidate.json').write_text(json.dumps(result,indent=2));print('Table:',len(selected),'groups;',result['physical_table_z_at_left_base_origin_m'],'m z-intercept')
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--workbench',type=Path,required=True);p.add_argument('--thickness-m',type=float,required=True);a=p.parse_args();fit(a.root,a.workbench,a.thickness_m)
