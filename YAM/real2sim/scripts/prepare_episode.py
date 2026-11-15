"""Normalize trusted local Xtrainer data and validate official USD frame conventions."""
import argparse
import json
import sys
from pathlib import Path
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from real2sim.xtrainer import normalize, fit_model_frames
from real2sim.usd_robot import UsdRobot

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source-root',type=Path,required=True)
parser.add_argument('--config',type=Path,default=ROOT/'configs/xtrainer_stack_cups.json')
parser.add_argument('--output',type=Path,default=ROOT/'runs/stack_cups')
args=parser.parse_args()
cfg=json.loads(args.config.read_text())
manifest=normalize(args.source_root,cfg,args.output)
robot=UsdRobot(ROOT/'assets/upstream/robots/x_trainer.usd')
data=np.load(args.output/'episode.npz')
frames={str(arm):fit_model_frames(robot,data['source_qpos'],arm) for arm in (1,2)}
(args.output/'model_alignment.json').write_text(json.dumps(frames,indent=2))
print(json.dumps({'frames':manifest['frame_count'],'alignment':frames},indent=2))
