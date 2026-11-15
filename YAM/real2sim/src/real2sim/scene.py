"""Reusable USD scene authoring helpers; no robot-specific conventions."""
import numpy as np
from pxr import UsdGeom, UsdShade, Sdf, Gf


def set_transform(prim, T, time=None):
    xf = UsdGeom.Xformable(prim)
    ops = xf.GetOrderedXformOps()
    if len(ops)!=1 or ops[0].GetOpType()!=UsdGeom.XformOp.TypeTransform:
        xf.ClearXformOpOrder()
        op = xf.AddTransformOp()
    else:
        op = ops[0]
    value = Gf.Matrix4d(*np.asarray(T,dtype=float).T.ravel())
    if time is None: op.Set(value)
    else: op.Set(value,float(time))


def material(stage, path, color, roughness=.65):
    mat = UsdShade.Material.Define(stage,path)
    shader = UsdShade.Shader.Define(stage,path+'/Preview')
    shader.CreateIdAttr('UsdPreviewSurface')
    shader.CreateInput('diffuseColor',Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*color))
    shader.CreateInput('roughness',Sdf.ValueTypeNames.Float).Set(roughness)
    mat.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(),'surface')
    return mat


def box(stage,path,center,size,mat):
    cube=UsdGeom.Cube.Define(stage,path)
    cube.CreateSizeAttr(1.)
    T=np.eye(4);T[:3,:3]=np.diag(size);T[:3,3]=center
    set_transform(cube.GetPrim(),T)
    UsdShade.MaterialBindingAPI.Apply(cube.GetPrim()).Bind(mat)
    return cube


def cup(stage,path,recipe,mat):
    """Continuous hollow frustum, closed bottom, open top. Metres."""
    radius=recipe['radius_top'];bottom=recipe['radius_bottom'];height=recipe['height']
    wall=recipe['wall'];n=recipe.get('segments',96)
    profile=[(bottom,0),(radius,height),(radius-wall,height),(bottom-wall,wall),(0,wall),(0,0)]
    vertices=[]
    for rad,z in profile:
        vertices.extend([(rad*np.cos(a),rad*np.sin(a),z) for a in np.linspace(0,2*np.pi,n,endpoint=False)])
    faces=[]
    for ring in range(len(profile)):
        next_ring=(ring+1)%len(profile)
        for j in range(n):
            faces.extend([ring*n+j,ring*n+(j+1)%n,next_ring*n+(j+1)%n,next_ring*n+j])
    root=UsdGeom.Xform.Define(stage,path)
    mesh=UsdGeom.Mesh.Define(stage,path+'/visual')
    mesh.CreatePointsAttr(vertices)
    mesh.CreateFaceVertexCountsAttr([4]*(len(faces)//4))
    mesh.CreateFaceVertexIndicesAttr(faces)
    mesh.CreateSubdivisionSchemeAttr('none')
    mesh.CreateDoubleSidedAttr(True)
    UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(mat)
    return root


def calibrated_camera(stage,path,K,size):
    K=np.asarray(K);w,h=size;f=20.
    camera=UsdGeom.Camera.Define(stage,path)
    camera.CreateFocalLengthAttr(f)
    camera.CreateHorizontalApertureAttr(w*f/K[0,0])
    camera.CreateVerticalApertureAttr(h*f/K[1,1])
    camera.CreateHorizontalApertureOffsetAttr((w/2-K[0,2])*f/K[0,0])
    camera.CreateVerticalApertureOffsetAttr((K[1,2]-h/2)*f/K[1,1])
    camera.CreateClippingRangeAttr(Gf.Vec2f(.005,20))
    return camera
