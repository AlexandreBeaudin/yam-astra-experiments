"""Offline eye-to-hand solve from platform-supplied poses, no robot connection."""
import argparse,sys
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from real2sim.calibration.handeye import solve_eye_to_hand,save_result
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--observations',type=Path,required=True,help='NPZ containing base_T_tool and camera_T_target, each N x 4 x 4 in metres');p.add_argument('--output',type=Path,required=True);a=p.parse_args()
x=np.load(a.observations,allow_pickle=False);A=x['base_T_tool'];B=x['camera_T_target']
if A.shape!=B.shape or A.ndim!=3 or A.shape[1:]!=(4,4) or not np.isfinite(A).all() or not np.isfinite(B).all():raise ValueError('Invalid rigid pose arrays')
r=solve_eye_to_hand(A,B,meta={'observations':str(a.observations),'platform':'provided_by_caller','status':'candidate_requires_holdout_validation'});save_result(r,str(a.output));print(a.output)
