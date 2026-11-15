"""Export goal and ball placement geometry as metric USD scenes (no physics claims)."""
import json,sys,hashlib
from pathlib import Path
import numpy as np,trimesh
from pxr import Usd,UsdGeom,UsdShade,Gf
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from real2sim.goal_placement import geometry
from real2sim.scene import calibrated_camera
run=ROOT/'runs/task_x_0001_supervision';data=json.loads((run/'manifest.json').read_text());out=run/'scenes';out.mkdir(exist_ok=True)
def mesh(stage,path,vertices,faces,color):
 m=UsdGeom.Mesh.Define(stage,path);m.CreatePointsAttr([Gf.Vec3f(*p) for p in vertices]);m.CreateFaceVertexCountsAttr([3]*len(faces));m.CreateFaceVertexIndicesAttr(np.array(faces).ravel().tolist());m.CreateSubdivisionSchemeAttr('none');m.CreateDisplayColorAttr([Gf.Vec3f(*color)])
 mat=UsdShade.Material.Define(stage,path+'_material');shader=UsdShade.Shader.Define(stage,path+'_material/Shader');shader.CreateIdAttr('UsdPreviewSurface')
 from pxr import Sdf
 shader.CreateInput('diffuseColor',Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*color));mat.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(),'surface');UsdShade.MaterialBindingAPI.Apply(m.GetPrim()).Bind(mat)
for l in data['layouts']:
 stage=Usd.Stage.CreateNew(str(out/(l['id']+'.usda')));UsdGeom.SetStageMetersPerUnit(stage,1);UsdGeom.SetStageUpAxis(stage,'Z');world=UsdGeom.Xform.Define(stage,'/World');stage.SetDefaultPrim(world.GetPrim());v,_=geometry(l,data['task']);pieces=[]
 for i,j in [(0,2),(1,3),(2,3),(0,4),(1,5),(4,6),(5,7),(6,7),(2,6),(3,7),(4,5)]:
  start=v[i].copy();end=v[j].copy();start[2]=max(start[2],data['task']['table_z']+data['task']['frame_m']/2);end[2]=max(end[2],data['task']['table_z']+data['task']['frame_m']/2);pieces.append(trimesh.creation.cylinder(radius=data['task']['frame_m']/2,segment=[start,end],sections=12))
 for f in np.linspace(0,1,13):
  for start,end in [(v[4]*(1-f)+v[5]*f,v[6]*(1-f)+v[7]*f),(v[4]*(1-f)+v[6]*f,v[5]*(1-f)+v[7]*f),(v[6]*(1-f)+v[7]*f,v[2]*(1-f)+v[3]*f)]:pieces.append(trimesh.creation.cylinder(radius=.0005,segment=[start+[0,0,.001],end+[0,0,.001]],sections=5))
 goal=trimesh.util.concatenate(pieces);mesh(stage,'/World/Goal',goal.vertices,goal.faces,(.8,.8,.8));sphere=trimesh.creation.icosphere(subdivisions=3,radius=data['task']['ball_radius_m']);sphere.apply_translation(l['ball_m']);mesh(stage,'/World/Ball',sphere.vertices,sphere.faces,(.9,.9,.9));stage.GetRootLayer().Save()
(out/'README.txt').write_text('Metric Z-up world, metres, left Xtrainer base coordinates. Goal and ball visual geometry only. No robot, camera, physics colliders or scoring validation. Camera parameters are in ../manifest.json. Goal net spacing and round frame cross-section are estimated. Ball markings are omitted.\n')
print('Exported',len(data['layouts']),'USD geometry scenes')
