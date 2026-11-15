"""Xtrainer-specific input and reference kinematics; no simulator imports."""
import hashlib
import json
import pickle
from pathlib import Path
import numpy as np
from scipy.optimize import least_squares
from .geometry import pose_vector, pose_error


def nova2_fk(q):
    # Source: existing platform calibration/fk.py, distances in metres.
    params = [(q[0], .2234, 0, np.pi/2), (q[1]-np.pi/2, 0, -.280, 0),
              (q[2], 0, -.225, 0), (q[3]-np.pi/2, .1175, 0, np.pi/2),
              (q[4], .120, 0, -np.pi/2), (q[5], .088, 0, 0)]
    T = np.eye(4)
    for theta, d, a, alpha in params:
        c, s, ca, sa = np.cos(theta), np.sin(theta), np.cos(alpha), np.sin(alpha)
        T = T @ np.array([[c,-s*ca,s*sa,a*c],[s,c*ca,-c*sa,a*s],[0,sa,ca,d],[0,0,0,1]])
    return T


def joint_map(q):
    conventions = json.loads((Path(__file__).resolve().parents[2]/'configs/xtrainer_usd_conventions.json').read_text())
    out = {}
    for arm, start in ((1,0),(2,7)):
        c = conventions[str(arm)]
        values = np.asarray(q[start:start+6])*c['signs']+c['offsets_rad']
        out.update({f'J{arm}_{j+1}':float(values[j]) for j in range(6)})
    for arm, col in ((1,6),(2,13)):
        # Upstream candidate mapping. Command-derived, not measured finger displacement.
        close = 1.0 - np.clip(q[col], 0, 1)
        out[f'J{arm}_7'], out[f'J{arm}_8'] = -.04*close, .04*close
    return out


def normalize(source_root, config, destination):
    """Only use with trusted local recordings: the legacy format is pickle."""
    root, destination = Path(source_root), Path(destination)
    episode = root / config['episode_path']
    paths = sorted((episode/'observation').glob('*.pkl'), key=lambda p: int(p.stem))
    if not paths:
        raise ValueError('No observation files')
    rows, commands, grippers, ids = [], [], [], []
    digest = hashlib.sha256()
    for path in paths:
        raw = path.read_bytes()
        digest.update(raw)
        obs = pickle.loads(raw)
        q, control = np.asarray(obs['joint_positions']), np.asarray(obs['control'])
        if q.shape != (14,) or control.shape != (14,) or not np.isfinite(q).all() or not np.isfinite(control).all():
            raise ValueError(f'Invalid state/action in {path}')
        for cam in config['cameras']:
            if not (episode/cam['image_dir']/f'{path.stem}.jpg').is_file():
                raise ValueError(f'Missing {cam["id"]} frame {path.stem}')
        rows.append(q); commands.append(control); grippers.append(obs['gripper_position']); ids.append(int(path.stem))
    if len(set(ids)) != len(ids):
        raise ValueError('Duplicate numeric frame ids')
    destination.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(destination/'episode.npz', frame_id=ids, source_qpos=rows,
                        action_position_target=commands, raw_gripper_position=grippers,
                        measured_arm_position=np.array(rows)[:,[0,1,2,3,4,5,7,8,9,10,11,12]])
    cameras = {}
    for cam in config['cameras']:
        intr = json.loads((root/cam['intrinsics']).read_text())
        K = np.array(intr['camera_matrix'])
        if cam['collector_flip_180']:
            K[0,2] = intr['image_size'][0]-1-K[0,2]
            K[1,2] = intr['image_size'][1]-1-K[1,2]
        cameras[cam['id']] = dict(K=K.tolist(), distortion=intr['dist_coeffs'], image_size=intr['image_size'],
                                 images=str((episode/cam['image_dir']).resolve()), flip_applied_to_intrinsics=cam['collector_flip_180'])
    manifest = dict(schema_version='0.1', episode_id=config['episode_id'], frame_count=len(ids),
                    source_sha256=digest.hexdigest(), source_episode=str(episode.resolve()), cameras=cameras,
                    time_source='frame ordinal only; no verified capture timestamps',
                    gripper_source='qpos columns 6/13 are command-derived; raw_gripper_position units unverified',
                    robot_joint_names=[f'J{a}_{j}' for a in (1,2) for j in range(1,7)])
    (destination/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    return manifest


def fit_model_frames(robot, qpos, arm):
    """Fit constant C,F such that USD_link(q) = C @ DH_flange(q) @ F.

    Fits coordinate conventions only, not individual joint/geometry parameters.
    Validation poses are kept separate from the fit poses.
    """
    path = next(j['child'] for j in robot.joints if j['name']==f'J{arm}_6')
    indices = np.linspace(0, len(qpos)-1, min(30,len(qpos))).astype(int)
    pairs = [(nova2_fk(qpos[i][0:6] if arm==1 else qpos[i][7:13]),
              robot.forward(joint_map(qpos[i]))[path]) for i in indices]
    def residual(v, sample_pairs):
        C, F = pose_vector(v[:6]), pose_vector(v[6:])
        return np.concatenate([pose_error(M,C@D@F) for D,M in sample_pairs])
    best = None
    for z in (0, np.pi/2, -np.pi/2, np.pi):
        v = np.zeros(12); v[2] = z
        fit = least_squares(residual,v,args=(pairs[::2],),max_nfev=400)
        if best is None or fit.cost<best.cost:
            best=fit
    validation = residual(best.x,pairs[1::2]).reshape(-1,6)
    return dict(C_usd_base=pose_vector(best.x[:6]).tolist(), F_flange_model_link=pose_vector(best.x[6:]).tolist(),
                validation_translation_mm_max=float(np.linalg.norm(validation[:,3:],axis=1).max()*1000),
                validation_rotation_deg_max=float(np.rad2deg(np.linalg.norm(validation[:,:3],axis=1)).max()),
                fitting='constant frame alignment; alternate held-out poses', link_path=path)
