"""Build visual candidates and measure actual USD geometry against named dimensions."""
import argparse,json,sys
from pathlib import Path
import numpy as np
import trimesh
from pxr import Usd,UsdGeom
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from real2sim.yam_drawer_visuals import build_asset

def bounds(stage,path):
 cache=UsdGeom.BBoxCache(Usd.TimeCode.Default(),[UsdGeom.Tokens.default_],useExtentsHint=False)
 r=cache.ComputeWorldBound(stage.GetPrimAtPath(path)).ComputeAlignedRange()
 return np.array(r.GetMin()),np.array(r.GetMax())

def check(stage,path,size):
 lo,hi=bounds(stage,path);np.testing.assert_allclose(hi-lo,size,atol=2e-7,rtol=0)
 return {'path':path,'expected_m':size,'actual_m':(hi-lo).tolist(),'min_m':lo.tolist(),'max_m':hi.tolist()}

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 if a.output.exists():p.error('Use a fresh audit directory')
 a.output.mkdir(parents=True);recipe=json.loads((ROOT/'configs/reconstruction/yam_task_0015_dimensions.json').read_text());results=[]
 for mid,item in recipe['materials'].items():
  file=a.output/(mid+'.usda');build_asset(file,mid,item['size_m']);stage=Usd.Stage.Open(str(file));size=item['size_m'];tests=[]
  if mid=='MAT-00050':
   g=item['geometry'];tests.append(check(stage,'/Asset/Body',size));tests.append(check(stage,'/Asset/Handle',[g['handle_projection_m'],g['handle_span_m'],g['handle_height_m']]))
   for i in range(3):
    lo,hi=bounds(stage,'/Asset/Drawer'+str(i));np.testing.assert_allclose(hi[2]-lo[2],g['drawer_height_m'],atol=2e-7,rtol=0)
    tests.append({'path':'/Asset/Drawer'+str(i),'height_m':float(hi[2]-lo[2])})
    tests.append(check(stage,'/Asset/Drawer'+str(i)+'/front_lower',[g['front_edge_thickness_m'],g['cavity_size_m'][1]-.002,g['drawer_height_m']-.012]))
   left=bounds(stage,'/Asset/Body/left')[1][1];right=bounds(stage,'/Asset/Body/right')[0][1]
   floor=bounds(stage,'/Asset/Body/bottom')[1][2];ceiling=bounds(stage,'/Asset/Body/top')[0][2];back=bounds(stage,'/Asset/Body/back')[0][0]
   cavity=[back+size[0]/2,right-left,ceiling-floor];np.testing.assert_allclose(cavity,g['cavity_size_m'],atol=2e-7,rtol=0);tests.append({'cavity_measured_m':cavity})
  else:tests.append(check(stage,'/Asset',size))
  if mid=='MAT-00048':
   g=item['geometry'];tests.append(check(stage,'/Asset/bowl',[g['bowl_length_m'],g['bowl_width_m'],size[2]]))
   lo,hi=bounds(stage,'/Asset/handle');np.testing.assert_allclose((hi-lo)[1:],[g['handle_width_m'],g['handle_thickness_m']],atol=2e-7,rtol=0)
   prim=UsdGeom.Mesh(stage.GetPrimAtPath('/Asset/bowl'));mesh=trimesh.Trimesh(vertices=np.array(prim.GetPointsAttr().Get()),faces=np.array(prim.GetFaceVertexIndicesAttr().Get()).reshape(-1,3),process=False)
   assert mesh.is_watertight and mesh.is_winding_consistent and mesh.volume>0
   tests.append({'closed_spoon_bowl':True,'min_triangle_area_m2':float(mesh.area_faces.min())});assert mesh.area_faces.min()>1e-10
  results.append({'material_id':mid,'checks':tests,'source':item['source'],'assumptions':item['assumptions']})
 report={'recipe_status':recipe['status'],'measured_usd_checks':results,'source_measurements_independently_verified':False}
 (a.output/'dimension_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'materials':len(results),'actual_usd_bounds_passed':True,'axis_confirmation':recipe['status']},ensure_ascii=False))
if __name__=='__main__':main()
