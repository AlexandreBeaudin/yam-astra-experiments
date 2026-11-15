"""Platform-neutral measured primitives, independent visual meshes and convex colliders."""
import numpy as np
import trimesh


def box(size, center):
    mesh = trimesh.creation.box(size)
    mesh.apply_translation(center)
    return mesh


def beam(start, end, radius, sections=16):
    return trimesh.creation.cylinder(radius=radius, segment=np.array([start, end]), sections=sections)


def geometry(spec):
    """Return [(visual mesh, RGB)], convex collision meshes, in metres, z-up."""
    x, y, z = np.asarray(spec['dimensions_mm'], float) / 1000
    color = spec['color']; kind = spec['kind']
    visuals, collisions = [], []
    def add(mesh, rgb=None, collide=True):
        visuals.append((mesh, color if rgb is None else rgb))
        if collide: collisions.append(mesh.copy())
    if kind == 'open_box':
        t = spec['wall_mm'] / 1000
        if not 0 < 2*t < min(x,y) or not t < z: raise ValueError('Invalid box wall thickness')
        add(box([x,y,t],[0,0,t/2]))
        for sign in [-1,1]:
            add(box([t,y,z-t],[sign*(x-t)/2,0,(z+t)/2]))
            add(box([x-2*t,t,z-t],[0,sign*(y-t)/2,(z+t)/2]))
    elif kind == 'ball':
        mesh = trimesh.creation.icosphere(subdivisions=3, radius=x/2)
        mesh.apply_translation([0,0,z/2]); add(mesh)
    elif kind == 'inkpad':
        add(box([x,y,z*.20],[0,0,z*.10]), [0.33,.025,.045])
        add(box([x,y,z*.80],[0,0,z*.60]))
    elif kind == 'stamp':
        h = spec['base_height_mm']/1000
        hx,hy,hz = np.array(spec['handle_dimensions_mm'])/1000
        add(box([x,y,h],[0,0,h/2]))
        rings = [(0,0,h),(.5,.5,h),(.31,.33,h+hz*.2),(.29,.31,h+hz*.72),(.5,.5,h+hz*.91),(.43,.45,z),(0,0,z)]
        n=64; vertices=[]
        for rx,ry,height in rings:
            vertices.extend([[rx*hx*np.cos(a),ry*hy*np.sin(a),height] for a in np.linspace(0,2*np.pi,n,endpoint=False)])
        faces=[]
        for i in range(len(rings)-1):
            for j in range(n):
                a=i*n+j;b=i*n+(j+1)%n;c=b+n;d=a+n
                faces.extend([[a,b,d],[b,c,d]])
        mesh=trimesh.Trimesh(vertices,faces,process=True)
        mesh.update_faces(mesh.nondegenerate_faces());mesh.remove_unreferenced_vertices();mesh.fix_normals()
        add(mesh,collide=False);collisions.append(mesh.convex_hull)
    elif kind == 'sausage':
        mesh=trimesh.creation.capsule(height=x-y,radius=y/2,count=[24,48])
        # Normalize the primitive to the registered overall package bounds.
        mesh.apply_translation(-mesh.bounds.mean(axis=0))
        mesh.apply_transform(trimesh.transformations.rotation_matrix(np.pi/2,[0,1,0]))
        mesh.vertices *= np.array([x,y,z])/mesh.extents
        mesh.apply_translation([0,0,z/2]);add(mesh)
    elif kind == 'goal':
        r=spec['frame_mm']/2000; front=-y/2+r; rear=y/2-r
        top=front+spec['top_depth_mm']/1000-2*r
        left=-x/2+r;right=x/2-r;low=r;high=z-r
        segments=[([left,front,low],[left,front,high]),([right,front,low],[right,front,high]),
                  ([left,front,high],[right,front,high]),([left,rear,low],[right,rear,low]),
                  ([left,top,high],[right,top,high])]
        for side in [left,right]:
            segments.extend([([side,front,low],[side,rear,low]),([side,front,high],[side,top,high]),([side,rear,low],[side,top,high])])
        for a,b in segments:add(beam(a,b,r))
        # Wire grid is visual only. Thin panel colliders approximate net contact.
        wire=.000225; pitch=.004
        for xx in np.arange(left+r,right-r,pitch):
            add(beam([xx,rear,low],[xx,top,high],wire,6),collide=False)
            add(beam([xx,front,high],[xx,top,high],wire,6),collide=False)
        for zz in np.arange(low+r,high-r,pitch):
            yy=rear+(top-rear)*(zz-low)/(high-low)
            add(beam([left,yy,zz],[right,yy,zz],wire,6),collide=False)
            for xx in [left,right]:add(beam([xx,front,zz],[xx,yy,zz],wire,6),collide=False)
        for yy in np.arange(front+r,top-r,pitch):
            add(beam([left,yy,high],[right,yy,high],wire,6),collide=False)
        for xx in [left,right]:
            for yy in np.arange(front+r,rear-r,pitch):
                zz=high if yy<=top else low+(high-low)*(rear-yy)/(rear-top)
                add(beam([xx,yy,low],[xx,yy,zz],wire,6),collide=False)
            points=[[xx+dx,yy,zz] for dx in [-wire,wire] for yy,zz in [(front,low),(rear,low),(top,high),(front,high)]]
            collisions.append(trimesh.convex.convex_hull(points))
        # Back panel follows the sloping back; front mouth has no collision plane.
        points=[[xx,yy+dy,zz] for xx in [left,right] for dy in [-wire,wire] for yy,zz in [(rear,low),(top,high)]]
        collisions.append(trimesh.convex.convex_hull(points))
        collisions.append(box([right-left,top-front,2*wire],[0,(front+top)/2,high]))
    else: raise ValueError('Unsupported recipe kind: '+kind)
    return visuals,collisions


def validate_geometry(spec, visuals, colliders):
    mesh=trimesh.util.concatenate([m for m,_ in visuals])
    expected=np.array(spec['dimensions_mm'])/1000
    error=float(np.max(np.abs(mesh.extents-expected)))
    report={'finite':bool(np.isfinite(mesh.vertices).all()),
            'watertight_parts':all(m.is_watertight for m,_ in visuals),
            'consistent_normals':all(m.is_winding_consistent for m,_ in visuals),
            'convex_colliders':all(m.is_watertight and m.is_convex for m in colliders),
            'bounds_error_m':error,'collider_count':len(colliders),'visual_parts':len(visuals)}
    report['passed']=all(report[k] for k in ['finite','watertight_parts','consistent_normals','convex_colliders']) and error<1e-5
    return report
