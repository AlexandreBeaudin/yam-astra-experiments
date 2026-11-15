"""Generate stratified layouts, or validate externally proposed JSON positions."""
import argparse, hashlib, json, sys, time
from pathlib import Path
import numpy as np
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT/'src'))
from real2sim.layouts import Camera, XtrainerReachability, propose, validate
from real2sim.distribution import default_policy,goal_schedule,near_duplicate,audit,enforce

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--count', type=int, help='Explicit unsplit diagnostic count; default 100 train + 30 validation')
    parser.add_argument('--train-count', type=int);parser.add_argument('--validation-count', type=int)
    parser.add_argument('--seed', type=int, default=20260914)
    parser.add_argument('--proposals', type=Path, help='JSON list of {positions_m: [[x,y,z],...], rationale: str}')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.count is not None and not 1 <= args.count <= 500: parser.error('count must be 1..500')
    if args.count is not None and (args.train_count is not None or args.validation_count is not None):parser.error('Choose split counts or unsplit --count')
    args.output=args.output or ROOT/'runs'/('stack_cups_distribution_'+time.strftime('%Y%m%d_%H%M%S'))
    if (args.output/'layouts.json').exists():parser.error('Use a new output directory; existing versions are immutable')
    pol=default_policy()
    for name,value in [('train',args.train_count),('validation',args.validation_count)]:
        if value is not None:
            if value<9:parser.error('Each split must cover all nine strata')
            pol['counts'][name]=value
    pol['seeds']={'train':args.seed,'validation':args.seed+104729}
    split_mode=args.count is None and args.proposals is None
    total=sum(pol['counts'].values()) if split_mode else (args.count or 36)
    task = json.loads((ROOT/'configs/tasks/stack_cups_distribution.json').read_text())
    registry = json.loads((ROOT/f'assets/registry/{task["asset_id"]}.{task["asset_version"]}.json').read_text())
    geometry = json.loads((ROOT/registry['geometry_recipe']).read_text())
    if any(geometry[k] != v for k, v in task['cup'].items()):
        raise ValueError('Task dimensions disagree with registered asset geometry')
    manifest = json.loads((ROOT/'runs/stack_cups/manifest.json').read_text())
    tracks = np.load(ROOT/'runs/stack_cups/scene_tracks.npz')
    cam = Camera(manifest['cameras']['top'], tracks['camera_top'][0])
    reach = XtrainerReachability(ROOT)
    rng = np.random.default_rng(args.seed)
    supplied = json.loads(args.proposals.read_text()) if args.proposals else None
    accepted, rejected = [], []
    # Round-robin quota per semantic stratum, with bounded retries and gaps reported.
    quota = [(total//9 + (i < total%9)) for i in range(9)]
    coverage = [0]*9
    proposals = len(supplied) if supplied is not None else total*80
    if split_mode:
        rngs={s:np.random.default_rng(seed) for s,seed in pol['seeds'].items()}
        for row in goal_schedule(pol):
            for attempt in range(3000):
                positions,tags=propose(rngs[row['split']],task,row['bin']);candidate={'positions_m':positions.tolist()}
                if any(near_duplicate(candidate,prior,pol) for prior in accepted):continue
                check=validate(positions,task,cam,reach)
                if check['passed']:break
                rejected.append({'split':row['split'],'bin':row['bin'],'reasons':check['reasons']})
            else:raise ValueError('Cannot fill requested split stratum; no partial bundle published')
            ident=f'layout_{len(accepted):03d}'
            accepted.append(dict(id=ident,positions_m=positions.tolist(),tags=tags,bin=row['bin'],validation=check,split=row['split'],split_group_id=f"stack_cups:{pol['seeds'][row['split']]}:{ident}"))
            coverage[row['bin']]+=1;print(f'accepted {len(accepted)}/{total}',flush=True)
    else:
        for attempt in range(proposals):
            if supplied is not None:
                entry = supplied[attempt]
                positions = entry.get('positions_m', [])
                tags = dict(family='external_proposal', rationale=str(entry.get('rationale', ''))[:2000])
            else:
                available = [i for i in range(9) if coverage[i] < quota[i]]
                if not available: break
                stratum = available[attempt % len(available)]
                positions, tags = propose(rng, task, stratum)
            check = validate(positions, task, cam, reach)
            if check['passed']:
                # Avoid effectively duplicated layouts (joint three-cup XY distance).
                if any(np.linalg.norm(np.array(a['positions_m'])[:, :2]-np.array(positions)[:, :2]) < .035 for a in accepted):
                    rejected.append(dict(attempt=attempt, reasons=['near_duplicate'])); continue
                accepted.append(dict(id=f'layout_{len(accepted):03d}', positions_m=np.asarray(positions).tolist(), tags=tags, validation=check))
                if supplied is None: coverage[stratum] += 1
                print(f'accepted {len(accepted)} / attempt {attempt+1} {tags}', flush=True)
            else:
                rejected.append(dict(attempt=attempt, reasons=check['reasons']))
    assets = ['assets/registry/paper_cup.v1.json', 'assets/cups/paper_cup/v1/asset.usda', 'configs/xtrainer_usd_conventions.json', 'configs/tasks/stack_cups_distribution.json', 'runs/stack_cups/scene_tracks.npz', 'runs/stack_cups/manifest.json']
    output = dict(schema_version='1.0', task=task, seed=args.seed,
                  proposer='external_JSON' if supplied is not None else 'deterministic_semantic_stratified_sampler',
                  vlm_called=False, camera=dict(**manifest['cameras']['top'], world_T_camera=cam.T.tolist()),
                  assets_sha256={p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in assets},
                  layouts=accepted, rejected=rejected, coverage_counts=coverage, coverage_quota=quota,
                  scope='geometric and 6D waypoint IK; static settling and RTX visibility are separate checks',
                  baseline_positions_m=tracks['object_T'][0, :, :3, 3].tolist())
    if split_mode:output['split_policy']=pol
    output['distribution_audit']=audit(output);enforce(output)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output/'layouts.json').write_text(json.dumps(output, ensure_ascii=False, indent=2))
    print(json.dumps(dict(accepted=len(accepted), rejected=len(rejected), coverage=coverage), indent=2))
    if not accepted: raise SystemExit('No valid layouts')

if __name__ == '__main__': main()
