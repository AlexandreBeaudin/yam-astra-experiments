"""Conservative envelopes from explicitly recorded metric goal/ball parameters."""
import math


def goal_envelopes(layout, task):
    if task.get('adapter')!='goal_scoring':return []
    if not all(k in layout for k in ('goal_m','ball_m','goal_yaw_deg')):return []
    a=math.radians(layout['goal_yaw_deg']);c,s=math.cos(a),math.sin(a)
    g=layout['goal_m'];f=task['frame_m']/2;w=task['goal_width_m']/2;d=task['goal_depth_m'];h=task['goal_height_m']
    verts=[[g[0]+c*x-s*y,g[1]+s*x+c*y,g[2]+z] for z in (0,h) for x,y in [(-f,-w),(d,-w),(d,w),(-f,w)]]
    b=layout['ball_m'];r=task['ball_radius_m'];ball=[[b[0]+x,b[1]+y,b[2]+z] for z in (-r,r) for x,y in [(-r,-r),(r,-r),(r,r),(-r,r)]]
    return [{'instance_id':'goal','name':'足球网','vertices_m':verts},{'instance_id':'ball','name':'足球','vertices_m':ball}]
