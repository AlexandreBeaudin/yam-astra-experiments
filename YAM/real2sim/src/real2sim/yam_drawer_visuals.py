"""Photo-informed, metric visual candidates for TASK-YAM-0015. No dynamics."""
import json
from pathlib import Path
import numpy as np
import trimesh
from pxr import Usd, UsdGeom, UsdShade, Gf
from real2sim.scene import material, box, set_transform


def mesh(stage,path,vertices,faces,mat):
    prim=UsdGeom.Mesh.Define(stage,path)
    prim.CreatePointsAttr(np.asarray(vertices).tolist());prim.CreateFaceVertexCountsAttr([len(f) for f in faces])
    prim.CreateFaceVertexIndicesAttr(np.asarray(faces).ravel().tolist());prim.CreateSubdivisionSchemeAttr('none');prim.CreateDoubleSidedAttr(True)
    UsdShade.MaterialBindingAPI.Apply(prim.GetPrim()).Bind(mat)
    return prim


def ellipsoid(stage,path,center,size,mat):
    shape=UsdGeom.Sphere.Define(stage,path);shape.CreateRadiusAttr(1.)
    T=np.diag([*np.asarray(size)/2,1.]);T[:3,3]=center;set_transform(shape.GetPrim(),T)
    UsdShade.MaterialBindingAPI.Apply(shape.GetPrim()).Bind(mat)


def capsule_box(stage,path,center,size,mat):
    # Rounded footprint extruded vertically; no opaque cuboid approximation.
    x,y,z=size;r=min(x,y)*.28;points=[]
    for cx,cy,start in [(x/2-r,y/2-r,0),(-x/2+r,y/2-r,90),(-x/2+r,-y/2+r,180),(x/2-r,-y/2+r,270)]:
        for a in np.linspace(start,start+90,9,endpoint=False):points.append([cx+r*np.cos(np.deg2rad(a)),cy+r*np.sin(np.deg2rad(a))])
    points=np.asarray(points);n=len(points)
    vertices=np.vstack([np.c_[points,np.full(n,-z/2)],np.c_[points,np.full(n,z/2)]])+center
    faces=[]
    for i in range(1,n-1):faces.extend([[0,i+1,i],[n,n+i,n+i+1]])
    for i in range(n):j=(i+1)%n;faces.extend([[i,j,n+j],[i,n+j,n+i]])
    mesh(stage,path,vertices,faces,mat)


def build_asset(path,mid,size):
    recipe=json.loads((Path(__file__).resolve().parents[2]/'configs/reconstruction/yam_task_0015_dimensions.json').read_text())['materials'][mid]
    if not np.allclose(size,recipe['size_m'],atol=1e-9,rtol=0):raise ValueError('Dimensions disagree with reviewed recipe: '+mid)
    stage=Usd.Stage.CreateNew(str(path));UsdGeom.SetStageMetersPerUnit(stage,1);UsdGeom.SetStageUpAxis(stage,'Z')
    root=UsdGeom.Xform.Define(stage,'/Asset');stage.SetDefaultPrim(root.GetPrim())
    colors={'paper':(.48,.34,.17),'edge':(.24,.16,.08),'gold':(.8,.49,.075),'white':(.85,.86,.84),'black':(.018,.022,.025),'blue':(.09,.43,.67),'green':(.018,.26,.11),'yellow':(.85,.7,.11),'pink':(.84,.28,.49),'red':(.65,.08,.055),'gray':(.13,.14,.2),'mint':(.14,.65,.47)}
    mats={k:material(stage,'/Asset/Looks/'+k,v,.8 if k in ['paper','green','yellow'] else .45) for k,v in colors.items()}
    sx,sy,h=size
    def b(name,c,s,m):return box(stage,'/Asset/'+name,c,s,mats[m])
    if mid=='MAT-00050':
        g=recipe['geometry'];depth,width,height=size;inside_depth,inside_width,inside_height=g['cavity_size_m']
        side=(width-inside_width)/2;cap=(height-inside_height)/2;back=depth-inside_depth
        UsdGeom.Xform.Define(stage,'/Asset/Body');UsdGeom.Xform.Define(stage,'/Asset/Handle')
        b('Body/top',[0,0,height-cap/2],[depth,width,cap],'paper')
        b('Body/bottom',[0,0,cap/2],[depth,width,cap],'paper')
        for label,sign in [('left',-1),('right',1)]:b('Body/'+label,[0,sign*(width-side)/2,height/2],[depth,side,height],'paper')
        b('Body/back',[(depth-back)/2,0,height/2],[back,width,height],'paper')
        dh=g['drawer_height_m'];wall=g['drawer_wall_m'];front=g['front_edge_thickness_m']
        gap=(inside_height-3*dh)/4;dw=inside_width-.002;tray_back=depth/2-back-.001
        for i in range(3):
            prefix='Drawer'+str(i);UsdGeom.Xform.Define(stage,'/Asset/'+prefix)
            lower=cap+gap+i*(dh+gap);z=lower+dh/2;notch_width=.09;notch_height=.012
            # True open finger notch;75.4mm height and5.9mm folded front edge.
            b(prefix+'/front_lower',[-depth/2+front/2,0,lower+(dh-notch_height)/2],[front,dw,dh-notch_height],'paper')
            for j,sign in enumerate([-1,1]):
                b(prefix+'/front_upper'+str(j),[-depth/2+front/2,sign*(dw+notch_width)/4,lower+dh-notch_height/2],[front,(dw-notch_width)/2,notch_height],'paper')
            tray_front=-depth/2+front;tray_depth=tray_back-tray_front;cx=(tray_front+tray_back)/2
            b(prefix+'/bottom',[cx,0,lower+wall/2],[tray_depth,dw,wall],'paper')
            for j,sign in enumerate([-1,1]):b(prefix+'/side'+str(j),[cx,sign*(dw-wall)/2,z],[tray_depth,wall,dh],'paper')
            b(prefix+'/back',[tray_back-wall/2,0,z],[wall,dw,dh],'paper')
        # U handle dimensions are independent of the unmeasured mounting plate.
        z=cap+gap+2*(dh+gap)+dh/2;span=g['handle_span_m'];projection=g['handle_projection_m'];hh=g['handle_height_m'];section=g['handle_section_m']
        for j,sign in enumerate([-1,1]):
            b('Handle/post'+str(j),[-depth/2-projection/2,sign*(span-section)/2,z],[projection,section,hh],'gold')
        b('Handle/bar',[-depth/2-projection+section/2,0,z],[section,span,hh],'gold')
        b('backing_plate',[-depth/2-.0005,0,z],[.001,min(width*.8,span+.05),.045],'gold')
    elif mid=='MAT-00005':b('block',[0,0,h/2],size,'blue')
    elif mid=='MAT-00012':
        ico=trimesh.creation.icosphere(subdivisions=4,radius=sx/2)
        directions=trimesh.creation.icosphere(subdivisions=0,radius=1).vertices
        centers=ico.triangles_center;unit=centers/np.linalg.norm(centers,axis=1)[:,None]
        dark=(unit@directions.T).max(axis=1)>.945
        for name,select in [('black',dark),('white',~dark)]:mesh(stage,'/Asset/'+name,ico.vertices+[0,0,h/2],ico.faces[select],mats[name])
    elif mid=='MAT-00021':
        capsule_box(stage,'/Asset/foam',np.array([0,0,h*.42]),[sx,sy,h*.84],mats['yellow'])
        capsule_box(stage,'/Asset/scour',np.array([0,0,h*.91]),[sx,sy,h*.18],mats['green'])
    elif mid=='MAT-00037':
        capsule_box(stage,'/Asset/package',np.array([0,0,(h-.0004)/2]),[sx,sy,h-.0004],mats['pink'])
        b('label',[0,0,h-.0003],[sx*.45,sy*.78,.0002],'white')
        for i in range(3):b('label_line'+str(i),[-sx*.12+i*.009,0,h-.0001],[.002,sy*.5,.0002],'pink')
    elif mid=='MAT-00038':
        capsule_box(stage,'/Asset/body',np.array([0,0,h*.42]),[sx,sy,h*.84],mats['white'])
        capsule_box(stage,'/Asset/buttons_panel',np.array([sx*.17,0,h*.85]),[sx*.49,sy*.84,.002],mats['gray'])
        for row in range(4):
            for col in range(3):ellipsoid(stage,f'/Asset/key{row}_{col}',[sx*.02+row*.019,(col-1)*.015,h*.92],[.012,.012,h*.16],mats['red'])
        ellipsoid(stage,'/Asset/dpad',[-sx*.28,0,h*.87],[.038,.038,.006],mats['yellow'])
        ellipsoid(stage,'/Asset/dpad_center',[-sx*.28,0,h*.92],[.018,.018,.004],mats['red'])
        for j in [-1,1]:ellipsoid(stage,f'/Asset/function{j+1}',[-sx*.085,j*.019,h*.91],[.011,.013,.004],mats['mint'])
    elif mid=='MAT-00048':
        g=recipe['geometry'];rx=g['bowl_length_m']/2;ry=g['bowl_width_m']/2
        center=-sx/2+rx;thickness=g['bowl_vertical_thickness_m'];nr,na=10,64
        vertices=[];faces=[]
        for layer in range(2):
            offset=len(vertices);vertices.append([center,0,thickness if layer==0 else 0])
            for r in np.linspace(1/nr,1,nr):
                for angle in np.linspace(0,2*np.pi,na,endpoint=False):
                    z=(h-thickness)*r*r+(thickness if layer==0 else 0)
                    vertices.append([center+rx*r*np.cos(angle),ry*r*np.sin(angle),z])
            local=[]
            for k in range(na):local.append([offset,offset+1+k,offset+1+(k+1)%na])
            for j in range(nr-1):
                for k in range(na):
                    a=offset+1+j*na+k;b=offset+1+j*na+(k+1)%na;c=b+na;d=a+na
                    local.extend([[a,d,c],[a,c,b]])
            if layer==1:local=[f[::-1] for f in local]
            faces.extend(local)
        n=1+nr*na
        for k in range(na):
            a=1+(nr-1)*na+k;b=1+(nr-1)*na+(k+1)%na
            faces.extend([[b,a,a+n],[b,a+n,b+n]])
        mesh(stage,'/Asset/bowl',vertices,faces,mats['blue'])
        length=sx-.033;handle_t=g['handle_thickness_m']
        capsule_box(stage,'/Asset/handle',np.array([sx/2-length/2,0,h-handle_t/2]),[length,g['handle_width_m'],handle_t],mats['blue'])
    else:raise ValueError('Unreviewed material: '+mid)
    root.GetPrim().SetCustomDataByKey('dimension_recipe',json.dumps(recipe,ensure_ascii=False))
    stage.GetRootLayer().Save()
