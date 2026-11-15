"""Export a reusable hollow cup with separate convex wall colliders."""
import json,sys
from pathlib import Path
import numpy as np
import trimesh
from pxr import Usd,UsdGeom,UsdPhysics,Gf
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from real2sim.scene import cup,material,set_transform
recipe=json.loads((ROOT/'configs/stack_cups_recipe.json').read_text())['cup']
out=ROOT/'assets/cups/paper_cup/v1';out.mkdir(parents=True,exist_ok=True)
stage=Usd.Stage.CreateNew(str(out/'asset.usda'));UsdGeom.SetStageMetersPerUnit(stage,1.);UsdGeom.SetStageUpAxis(stage,'Z')
mat=material(stage,'/Cup/Materials/Paper',(.79,.77,.66),.85)
root=cup(stage,'/Cup',recipe,mat);stage.SetDefaultPrim(root.GetPrim())
UsdPhysics.RigidBodyAPI.Apply(root.GetPrim())
UsdPhysics.MassAPI.Apply(root.GetPrim()).CreateMassAttr(.008)
mesh=UsdGeom.Mesh(stage.GetPrimAtPath('/Cup/visual'));vertices=np.asarray(mesh.GetPointsAttr().Get());quads=np.asarray(mesh.GetFaceVertexIndicesAttr().Get()).reshape(-1,4)
faces=np.concatenate([quads[:,[0,1,2]],quads[:,[0,2,3]]])
visual=trimesh.Trimesh(vertices,faces,process=True);visual.visual.vertex_colors=[226,222,202,255];visual.export(out/'visual.glb')
collider_root=UsdGeom.Xform.Define(stage,'/Cup/Collision')
UsdGeom.Imageable(collider_root.GetPrim()).CreatePurposeAttr('guide')
segments=32
rt,rb,h,w=recipe['radius_top'],recipe['radius_bottom'],recipe['height'],recipe['wall']
for i in range(segments):
    angles=[2*np.pi*i/segments,2*np.pi*(i+1)/segments]
    points=[]
    for radius,z in [(rb,0),(rt,h),(rb-w,w),(rt-w,h)]:
        points.extend([(radius*np.cos(a),radius*np.sin(a),z) for a in angles])
    hull=trimesh.convex.convex_hull(np.array(points))
    collision=UsdGeom.Mesh.Define(stage,f'/Cup/Collision/Wall{i}')
    collision.CreatePointsAttr(hull.vertices.tolist());collision.CreateFaceVertexCountsAttr([3]*len(hull.faces));collision.CreateFaceVertexIndicesAttr(hull.faces.ravel().tolist())
    UsdPhysics.CollisionAPI.Apply(collision.GetPrim())
    UsdPhysics.MeshCollisionAPI.Apply(collision.GetPrim()).CreateApproximationAttr('convexHull')
bottom=UsdGeom.Cylinder.Define(stage,'/Cup/Collision/Bottom');bottom.CreateRadiusAttr(rb);bottom.CreateHeightAttr(w);bottom.CreateAxisAttr('Z')
T=np.eye(4);T[2,3]=w/2;set_transform(bottom.GetPrim(),T);UsdPhysics.CollisionAPI.Apply(bottom.GetPrim())
stage.GetRootLayer().Save()
(out/'recipe.json').write_text(json.dumps(recipe,indent=2))
(out/'physical.json').write_text(json.dumps({'mass_kg':.008,'source':'assumed, not measured','contact_material':'engine default until identified','physics_validated':False,'wall_colliders':segments,'bottom_collider':1},indent=2))
(out/'README.md').write_text('# Paper cup v1\n\nGenerated parameterized hollow cup. Units: metres. Dimensions are visual estimates.\n\n`visual.glb` is the visual mesh; `asset.usda` includes 32 independent convex wall colliders and a bottom collider, preserving the cavity.\nMass is assumed; contact physics is not validated. Regenerate with `scripts/build_cup_asset.py`.\n')
print(out)
