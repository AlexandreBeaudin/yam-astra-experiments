"""Render USD visual-state replay with Isaac Sim RTX; no physics rollout."""
import argparse
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--run',type=Path,default=ROOT/'runs/stack_cups')
parser.add_argument('--frames',type=int,nargs='+',default=[0,60,120,180,240,300,360,409])
parser.add_argument('--stride',type=int)
args=parser.parse_args()

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from real2sim.simulation_runtime import runtime_info, backend_label, close_app, close_app
RUNTIME = runtime_info()
from isaacsim import SimulationApp
app=SimulationApp({'fast_shutdown': True, 'headless':True,'width':640,'height':480,'anti_aliasing':0,'renderer':'RayTracedLighting'})
import json
import numpy as np
import cv2
import omni.usd
import omni.timeline
import omni.replicator.core as rep
import carb

settings=carb.settings.get_settings()
settings.set('/rtx/post/aa/op',0)
settings.set('/rtx/post/histogram/enabled',False)
settings.set('/rtx/post/tonemap/filmIso',100.)
try:
    omni.usd.get_context().open_stage(str((args.run/'scene.usda').resolve()))
    for _ in range(30):app.update()
    timeline=omni.timeline.get_timeline_interface()
    rep.orchestrator.set_capture_on_play(False)
    products={};annotators={}
    for name in ('top','left','right'):
        product=rep.create.render_product(f'/World/Cameras/{name}',(640,480))
        ann=rep.AnnotatorRegistry.get_annotator('rgb');ann.attach([product])
        products[name]=product;annotators[name]=ann
        (args.run/'renders'/name).mkdir(parents=True,exist_ok=True)
    count=json.loads((args.run/'manifest.json').read_text())['frame_count']
    frames=sorted(set(list(range(0,count,args.stride))+[count-1])) if args.stride else args.frames
    for idx,i in enumerate(frames):
        timeline.set_current_time(i/24.)
        app.update()
        rep.orchestrator.step(rt_subframes=8,delta_time=0.,pause_timeline=True)
        for name,ann in annotators.items():
            rgb=np.asarray(ann.get_data())
            if rgb.shape[:2]!=(480,640) or rgb.size==0:raise RuntimeError(f'Empty render {name} frame {i}: {rgb.shape}')
            if np.std(rgb[:,:,:3])<1:raise RuntimeError(f'Uniform/blocked render {name} frame {i}')
            cv2.imwrite(str(args.run/'renders'/name/f'{i:04d}.png'),cv2.cvtColor(rgb[:,:,:3],cv2.COLOR_RGB2BGR))
        print(f'RENDERED {idx+1}/{len(frames)} frame={i}',flush=True)
    (args.run/'render_manifest.json').write_text(json.dumps({'backend':backend_label(),'runtime':RUNTIME,'mode':json.loads((args.run/'scene_manifest.json').read_text()).get('mode','visual_state'),'frames':frames,'physics_stepped':False},indent=2))
finally:
    close_app(app)
