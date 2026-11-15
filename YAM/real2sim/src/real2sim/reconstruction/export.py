"""Portable USD/GLB and MuJoCo asset export, with independently authored collisions."""
import json
from pathlib import Path
import xml.etree.ElementTree as ET
import numpy as np
import trimesh
from pxr import Usd, UsdGeom, UsdPhysics, UsdShade
from real2sim.scene import material


def export_asset(out, spec, visuals, colliders):
    stage=Usd.Stage.CreateNew(str(out/'asset.usda'))
    UsdGeom.SetStageMetersPerUnit(stage,1.);UsdGeom.SetStageUpAxis(stage,'Z')
    root=UsdGeom.Xform.Define(stage,'/Asset');stage.SetDefaultPrim(root.GetPrim())
    UsdPhysics.RigidBodyAPI.Apply(root.GetPrim())
    UsdPhysics.MassAPI.Apply(root.GetPrim()).CreateMassAttr(spec['mass_kg'])
    contact=UsdShade.Material.Define(stage,'/Asset/Materials/Contact')
    api=UsdPhysics.MaterialAPI.Apply(contact.GetPrim());api.CreateStaticFrictionAttr(.5);api.CreateDynamicFrictionAttr(.4);api.CreateRestitutionAttr(0.)
    def usd_mesh(path, mesh, rgb, collision=False):
        p=UsdGeom.Mesh.Define(stage,path);p.CreatePointsAttr(mesh.vertices.tolist())
        p.CreateFaceVertexCountsAttr([3]*len(mesh.faces));p.CreateFaceVertexIndicesAttr(mesh.faces.ravel().tolist())
        p.CreateSubdivisionSchemeAttr('none')
        if collision:
            UsdPhysics.CollisionAPI.Apply(p.GetPrim())
            UsdPhysics.MeshCollisionAPI.Apply(p.GetPrim()).CreateApproximationAttr('convexHull')
            UsdShade.MaterialBindingAPI.Apply(p.GetPrim()).Bind(contact,UsdShade.Tokens.weakerThanDescendants,'physics')
            p.CreatePurposeAttr('guide')
        else:
            mat=material(stage,path.replace('/Visual/','/Materials/'),rgb,.7)
            UsdShade.MaterialBindingAPI.Apply(p.GetPrim()).Bind(mat)
        return p
    scene=trimesh.Scene()
    mj=ET.Element('mujoco',model=spec['id'])
    ET.SubElement(mj,'compiler',angle='radian',autolimits='true')
    ET.SubElement(mj,'option',timestep=str(1/240),gravity='0 0 -9.81',iterations='100')
    ET.SubElement(mj,'visual');asset=ET.SubElement(mj,'asset');world=ET.SubElement(mj,'worldbody')
    body=ET.SubElement(world,'body',name='object');ET.SubElement(body,'freejoint',name='free')
    # Explicit uniform-visual-volume inertia, not inferred from overlapping collision meshes.
    combined=trimesh.util.concatenate([m for m,_ in visuals])
    center=combined.center_mass
    inertia=combined.moment_inertia*(spec['mass_kg']/combined.mass)
    if spec['kind']=='ball':
        radius=spec['dimensions_mm'][0]/2000
        center=np.array([0,0,radius]);inertia=np.eye(3)*(.4*spec['mass_kg']*radius**2)
    ET.SubElement(body,'inertial',pos=' '.join(map(str,center)),mass=str(spec['mass_kg']),
        fullinertia=' '.join(map(str,[inertia[0,0],inertia[1,1],inertia[2,2],inertia[0,1],inertia[0,2],inertia[1,2]])))
    for i,(mesh,rgb) in enumerate(visuals):
        p=usd_mesh(f'/Asset/Visual/Part{i}',mesh,rgb)
        colored=mesh.copy();colored.visual.face_colors=np.array([*rgb,1])*255
        # Pattern is a geometric approximation, consistent across USD and GLB.
        if spec['kind']=='ball':
            directions=mesh.triangles_center-np.array([0,0,spec['dimensions_mm'][2]/2000])
            directions/=np.linalg.norm(directions,axis=1)[:,None]
            anchors=trimesh.creation.icosphere(subdivisions=0).vertices
            black=np.flatnonzero((directions@anchors.T).max(axis=1)>.955)
            colored.visual.face_colors[black]=[8,8,10,255]
            subset=UsdGeom.Subset.Define(stage,f'/Asset/Visual/Part{i}/Patches')
            subset.CreateElementTypeAttr('face');subset.CreateIndicesAttr(black.tolist());subset.CreateFamilyNameAttr('materialBind')
            UsdShade.MaterialBindingAPI.Apply(subset.GetPrim()).Bind(material(stage,'/Asset/Materials/Patches',(.03,.03,.04)))
        scene.add_geometry(colored,node_name=f'part{i}')
    # Merge visuals by color to keep MuJoCo mesh/geom counts small. Pattern faces get their own mesh.
    groups={}
    for mesh,rgb in visuals:groups.setdefault(tuple(rgb),[]).append(mesh)
    if spec['kind']=='ball':
        mesh=visuals[0][0];face_mask=np.ones(len(mesh.faces),bool);face_mask[black]=False
        groups={tuple(spec['color']):[mesh.submesh([np.flatnonzero(face_mask)],append=True)],(.03,.03,.04):[mesh.submesh([black],append=True)]}
    (out/'visual').mkdir();(out/'collision').mkdir()
    for i,(rgb,meshes) in enumerate(groups.items()):
        mesh=trimesh.util.concatenate(meshes);file=f'visual/part{i}.obj';mesh.export(out/file)
        ET.SubElement(asset,'mesh',name=f'visual{i}',file=file)
        ET.SubElement(body,'geom',type='mesh',mesh=f'visual{i}',contype='0',conaffinity='0',group='1',mass='0',rgba=' '.join(map(str,[*rgb,1])))
    for i,mesh in enumerate(colliders):
        if spec['kind']=='ball':
            sphere=UsdGeom.Sphere.Define(stage,f'/Asset/Collision/Part{i}')
            sphere.CreateRadiusAttr(radius);sphere.AddTranslateOp().Set((0.,0.,radius))
            sphere.CreatePurposeAttr('guide');UsdPhysics.CollisionAPI.Apply(sphere.GetPrim())
            UsdShade.MaterialBindingAPI.Apply(sphere.GetPrim()).Bind(contact,UsdShade.Tokens.weakerThanDescendants,'physics')
            ET.SubElement(body,'geom',type='sphere',size=str(radius),pos=f'0 0 {radius}',group='3',mass='0',rgba='0 0 0 0',friction='.5 .005 .0001',solref='.005 1',margin='0')
            continue
        usd_mesh(f'/Asset/Collision/Part{i}',mesh,None,True)
        file=f'collision/part{i}.obj';mesh.export(out/file)
        ET.SubElement(asset,'mesh',name=f'collision{i}',file=file)
        ET.SubElement(body,'geom',type='mesh',mesh=f'collision{i}',group='3',mass='0',rgba='0 0 0 0',friction='.5 .005 .0001',solref='.005 1',margin='0',gap='0')
    stage.GetRootLayer().Save();scene.export(out/'visual.glb')
    ET.indent(mj);ET.ElementTree(mj).write(out/'asset.mjcf.xml',encoding='unicode')
    (out/'physical.json').write_text(json.dumps({'mass_kg':spec['mass_kg'],'mass_source':'assumed, not measured',
        'center_of_mass_m':center.tolist(),'inertia_kg_m2':inertia.tolist(),
        'inertia_source':('analytic uniform sphere' if spec['kind']=='ball' else 'uniform density per visual-part volume; overlaps not Boolean-unioned'),
        'usd_static_friction':.5,'usd_dynamic_friction':.4,'mujoco_friction':[.5,.005,.0001],
        'friction_source':'assumed smoke-test values; backend contact models are not equivalent'},indent=2))
    (out/'frames.json').write_text(json.dumps({'units':'m','up_axis':'z','origin':'center of external XY bounds at lowest support plane',
        'object_frame_to_grasp':None,'grasp_points_source':'not calibrated'},indent=2))
