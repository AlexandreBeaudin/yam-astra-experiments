"""Build explicit parametric candidates from a pinned Feishu snapshot and recipe.

This is a model-assisted recipe compiler, not an automatic general image-to-3D model.
"""
import argparse,hashlib,json,sys,urllib.request,urllib.error
from pathlib import Path
import cv2,numpy as np,trimesh
from pxr import Usd,UsdGeom,UsdPhysics,UsdShade,Sdf,Gf
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from real2sim.scene import material,set_transform
from real2sim.feishu import Client,API

def mesh_prim(stage,path,mesh,mat=None,collision=False):
    p=UsdGeom.Mesh.Define(stage,path);p.CreatePointsAttr(mesh.vertices.tolist())
    p.CreateFaceVertexCountsAttr([3]*len(mesh.faces));p.CreateFaceVertexIndicesAttr(mesh.faces.ravel().tolist())
    p.CreateSubdivisionSchemeAttr('none');p.CreateDoubleSidedAttr(True)
    if mat:UsdShade.MaterialBindingAPI.Apply(p.GetPrim()).Bind(mat)
    if collision:
        UsdPhysics.CollisionAPI.Apply(p.GetPrim());UsdPhysics.MeshCollisionAPI.Apply(p.GetPrim()).CreateApproximationAttr('convexHull')
        p.CreatePurposeAttr('guide')
    return p

def rings_mesh(rings,n=96):
    # Each ring is radius_x, radius_y, height. Cap axis rings are merged by process=True.
    theta=np.linspace(0,2*np.pi,n,endpoint=False)
    vertices=np.array([[rx*np.cos(a),ry*np.sin(a),z] for rx,ry,z in rings for a in theta])
    faces=[]
    for i in range(len(rings)-1):
        for k in range(n):
            a=i*n+k;b=i*n+(k+1)%n;c=b+n;d=a+n
            faces.extend([[a,b,d],[b,c,d]])
    m=trimesh.Trimesh(vertices,faces,process=True);m.update_faces(m.nondegenerate_faces());m.remove_unreferenced_vertices();m.fix_normals()
    return m

def build_geometry(spec):
    x,y,z=np.array(spec['dimensions_mm'])/1000
    kind=spec['kind']
    if kind=='box':
        m=trimesh.creation.box([x,y,z]);m.apply_translation([0,0,z/2]);return m,[m.copy()]
    if kind=='sphere':
        m=trimesh.creation.icosphere(subdivisions=4,radius=x/2);m.apply_translation([0,0,z/2]);return m,[m.copy()]
    if kind=='cylinder':
        m=trimesh.creation.cylinder(radius=x/2,height=z,sections=96);m.apply_translation([0,0,z/2]);return m,[m.copy()]
    if kind=='bottle':
        cap=spec['cap_diameter_mm']/2000;body=spec['body_height_mm']/1000
        rings=[(0,0,0),(x*.44,y*.44,0),(x/2,y/2,z*.04),(x/2,y/2,body*.84),
               (cap,cap,body),(cap,cap,z),(0,0,z)]
        m=rings_mesh(rings)
        return m,[m.convex_hull]
    if kind=='cup':
        rb=spec['bottom_diameter_mm']/2000;it=spec['inner_top_diameter_mm']/2000;ib=spec['inner_bottom_diameter_mm']/2000;b=spec['bottom_thickness_mm']/1000
        rings=[(0,0,0),(rb,rb,0),(x/2,x/2,z),(it,it,z),(ib,ib,b),(0,0,b)]
        m=rings_mesh(rings);colliders=[]
        for i in range(32):
            angles=[2*np.pi*i/32,2*np.pi*(i+1)/32]
            points=np.array([[r*np.cos(a),r*np.sin(a),h] for r,h in [(rb,0),(x/2,z),(it,z),(ib,b)] for a in angles])
            colliders.append(trimesh.convex.convex_hull(points))
        bottom=trimesh.creation.cylinder(radius=rb,height=b,sections=64);bottom.apply_translation([0,0,b/2]);colliders.append(bottom)
        return m,colliders
    raise ValueError(kind)

def add_texture_face(stage,out,spec,photo):
    # Manually inspected front/top quadrilaterals, not calibrated multiview reconstruction.
    if spec['id'] not in ('MAT-00036','MAT-00033'):return None
    x,y,z=np.array(spec['dimensions_mm'])/1000
    if spec['id']=='MAT-00036':
        quad=[[175,586],[753,595],[743,1350],[190,1320]]
        points=[[-x/2,-y/2-.00005,z],[x/2,-y/2-.00005,z],[x/2,-y/2-.00005,0],[-x/2,-y/2-.00005,0]]
    else:
        quad=[[714,399],[1303,406],[1340,542],[657,543]]
        points=[[-x/2,y/2,z+.00005],[x/2,y/2,z+.00005],[x/2,-y/2,z+.00005],[-x/2,-y/2,z+.00005]]
    image=cv2.imread(str(photo));H=cv2.getPerspectiveTransform(np.array(quad,np.float32),np.array([[0,0],[511,0],[511,511],[0,511]],np.float32))
    cv2.imwrite(str(out/'visible_face.png'),cv2.warpPerspective(image,H,(512,512)))
    mat=UsdShade.Material.Define(stage,'/Asset/Materials/Photo')
    shader=UsdShade.Shader.Define(stage,'/Asset/Materials/Photo/Surface');shader.CreateIdAttr('UsdPreviewSurface')
    tex=UsdShade.Shader.Define(stage,'/Asset/Materials/Photo/Texture');tex.CreateIdAttr('UsdUVTexture')
    tex.CreateInput('file',Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath('visible_face.png'));tex.CreateInput('sourceColorSpace',Sdf.ValueTypeNames.Token).Set('sRGB')
    uv=UsdShade.Shader.Define(stage,'/Asset/Materials/Photo/UV');uv.CreateIdAttr('UsdPrimvarReader_float2');uv.CreateInput('varname',Sdf.ValueTypeNames.Token).Set('st')
    tex.CreateInput('st',Sdf.ValueTypeNames.Float2).ConnectToSource(uv.ConnectableAPI(),'result')
    shader.CreateInput('diffuseColor',Sdf.ValueTypeNames.Color3f).ConnectToSource(tex.ConnectableAPI(),'rgb');shader.CreateInput('roughness',Sdf.ValueTypeNames.Float).Set(.7)
    mat.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(),'surface')
    face=UsdGeom.Mesh.Define(stage,'/Asset/PhotoFace');face.CreatePointsAttr(points);face.CreateFaceVertexCountsAttr([4]);face.CreateFaceVertexIndicesAttr([0,1,2,3]);face.CreateDoubleSidedAttr(True);face.CreateSubdivisionSchemeAttr('none')
    UsdGeom.PrimvarsAPI(face).CreatePrimvar('st',Sdf.ValueTypeNames.TexCoord2fArray,UsdGeom.Tokens.vertex).Set([(0,1),(1,1),(1,0),(0,0)])
    UsdShade.MaterialBindingAPI.Apply(face.GetPrim()).Bind(mat)
    return {'source_quad_pixels':quad,'method':'manually selected planar visible-face homography','scope':'one visible face only; photographed lighting retained'}

def main():
    p=argparse.ArgumentParser();p.add_argument('--download',action='store_true');a=p.parse_args()
    cfg=json.loads((ROOT/'configs/reconstruction/first_batch.json').read_text())
    snapshot=ROOT/'runs/feishu_materials/latest.json';data=json.loads(snapshot.read_text());by_id={m['material_id']:m for m in data['materials']}
    run=ROOT/'runs/material_reconstruction_batch';(run/'input').mkdir(parents=True,exist_ok=True)
    (run/'source_snapshot.json').write_bytes(snapshot.read_bytes())
    client=None
    if a.download:client=Client();client.authenticate()
    output=[]
    for spec in cfg['items']:
        mid=spec['id'];source=by_id[mid];photo=run/'input'/f'{mid}.jpg'
        if source['key_dimensions'] != spec['expected_dimensions_text']:
            raise ValueError(f'{mid}: dimensions changed since recipe review; review recipe before rebuilding')
        if a.download:
            attachment=source['photos'][0]
            req=urllib.request.Request(API+'/drive/v1/medias/'+attachment['file_token']+'/download',headers={'Authorization':'Bearer '+client.token})
            with client.opener.open(req,timeout=30) as response:raw=response.read()
            if cv2.imdecode(np.frombuffer(raw,np.uint8),cv2.IMREAD_COLOR) is None:raise ValueError('Attachment is not an image')
            photo.write_bytes(raw)
        if not photo.exists():raise ValueError(f'Photo missing: {mid}; run with --download')
        mesh,colliders=build_geometry(spec)
        out=ROOT/'assets/materials'/mid/cfg['version'];out.mkdir(parents=True,exist_ok=True)
        stage=Usd.Stage.CreateNew(str(out/'asset.usda'));UsdGeom.SetStageMetersPerUnit(stage,1.);UsdGeom.SetStageUpAxis(stage,'Z')
        root=UsdGeom.Xform.Define(stage,'/Asset');stage.SetDefaultPrim(root.GetPrim())
        UsdPhysics.RigidBodyAPI.Apply(root.GetPrim());UsdPhysics.MassAPI.Apply(root.GetPrim()).CreateMassAttr(spec['mass_kg'])
        mat=material(stage,'/Asset/Materials/Base',spec['color'],.7);visual=mesh_prim(stage,'/Asset/Visual',mesh,mat)
        if spec['kind']=='sphere':
            centers=mesh.triangles_center-[0,0,spec['dimensions_mm'][2]/2000]
            directions=centers/np.linalg.norm(centers,axis=1,keepdims=True)
            anchors=trimesh.creation.icosphere(subdivisions=0).vertices
            black=np.flatnonzero((directions@anchors.T).max(axis=1)>.955)
            subset=UsdGeom.Subset.Define(stage,'/Asset/Visual/BlackPatches');subset.CreateElementTypeAttr('face');subset.CreateIndicesAttr(black.tolist());subset.CreateFamilyNameAttr('materialBind')
            UsdShade.MaterialBindingAPI.Apply(subset.GetPrim()).Bind(material(stage,'/Asset/Materials/Black',(.01,.01,.015)))
        for i,c in enumerate(colliders):mesh_prim(stage,f'/Asset/Collision/Part{i}',c,collision=True)
        texture=add_texture_face(stage,out,spec,photo)
        stage.GetRootLayer().Save()
        mesh.visual.vertex_colors=np.array([*spec['color'],1])*255;mesh.export(out/'visual.glb')
        actual=mesh.extents;expected=np.array(spec['dimensions_mm'])/1000
        geometry_pass=bool(mesh.is_watertight and mesh.is_winding_consistent and np.isfinite(mesh.vertices).all() and np.max(abs(actual-expected))<1e-5 and all(c.is_watertight and c.is_convex for c in colliders))
        record=dict(material_id=mid,name=source['name'],version=cfg['version'],kind=spec['kind'],asset_usd=str(out/'asset.usda'),visual_glb=str(out/'visual.glb'),
                    photo=str(photo),source_record_id=source['source_record_id'],source_dimensions=source['key_dimensions'],dimensions_source=source['dimensions_source'],
                    source_snapshot_sha256=hashlib.sha256(snapshot.read_bytes()).hexdigest(),photo_sha256=hashlib.sha256(photo.read_bytes()).hexdigest(),
                    dimensions_m=actual.tolist(),recipe=spec,texture=texture,mass_source='assumed test value, not measured',
                    mesh_validation=dict(passed=geometry_pass,watertight=bool(mesh.is_watertight),winding_consistent=bool(mesh.is_winding_consistent),collider_count=len(colliders),bounds_error_m=float(np.max(abs(actual-expected)))),
                    quality='candidate_pending_visual_review',contact_validated=False,
                    limitations=spec['assumptions']+['rigid bodies; mass/friction unmeasured','single view, no depth correction','GLB preview is geometry/base-color only; USD contains photo-face and patterned materials'])
        (out/'manifest.json').write_text(json.dumps(record,ensure_ascii=False,indent=2));output.append(record)
        print(mid,'mesh_pass',geometry_pass,flush=True)
    (run/'batch.json').write_text(json.dumps({'assets':output,'simfoundry_code_executed':False,'method':'assistant-inspected parametric recipes, metric constraints, separate colliders, validation stages'},ensure_ascii=False,indent=2))
    if not all(x['mesh_validation']['passed'] for x in output):raise SystemExit('Geometry check failed')

if __name__=='__main__':main()
