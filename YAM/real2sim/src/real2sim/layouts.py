"""Stack-cups task geometry; compatibility imports for earlier entry points."""
import cv2
import numpy as np
from .camera import Camera

def __getattr__(name):
    if name == "XtrainerReachability":
        from .platforms.xtrainer import XtrainerReachability
        return XtrainerReachability
    raise AttributeError(name)

def cup_surface(position, recipe):
    angles = np.linspace(0, 2*np.pi, 96, endpoint=False)
    return np.array([np.asarray(position) + [r*np.cos(a), r*np.sin(a), z]
                     for r, z in [(recipe['radius_bottom'], 0), (recipe['radius_top'], recipe['height'])]
                     for a in angles])


def validate(positions, task, camera, reach=None):
    p = np.asarray(positions, float)
    result = dict(passed=False, reasons=[], waypoint_ik=[], checks={})
    if p.shape != (3, 3) or not np.isfinite(p).all():
        result['reasons'] = ['invalid_positions']; return result
    r = task['cup']['radius_top']; bounds = np.array(task['table_bounds_xy'])
    result['checks']['on_table'] = bool(np.all(p[:, :2]-r >= bounds[:, 0]) and np.all(p[:, :2]+r <= bounds[:, 1]) and np.allclose(p[:, 2], task['table_z'], atol=.001))
    distances = np.linalg.norm(p[:, None, :2]-p[None, :, :2], axis=-1)
    result['checks']['separation'] = bool(np.min(distances[np.triu_indices(3, 1)]) >= 2*r+task['clearance_m'])
    masks = []
    in_view = True
    for point in p:
        uv, depth = camera.project(cup_surface(point, task['cup']))
        w, h = camera.size; margin = task['image_margin_px']
        in_view &= bool(np.all(depth > .005) and np.all(uv >= margin) and np.all(uv <= [w-1-margin, h-1-margin]))
        mask = np.zeros((h, w), np.uint8)
        cv2.fillConvexPoly(mask, cv2.convexHull(np.clip(uv, -10000, 10000).astype(np.int32)), 1)
        masks.append(mask)
    result['checks']['complete_top_frustum'] = in_view
    result['checks']['no_cup_image_overlap'] = not bool(np.any(np.sum(masks, axis=0)>1))
    result['reasons'] = [k for k, v in result['checks'].items() if not v]
    if not result['reasons'] and reach is not None:
        valid, checks = reach.check(p)
        result['waypoint_ik'] = checks
        result['checks']['waypoint_ik'] = valid
        if not valid: result['reasons'].append('waypoint_ik')
    result['passed'] = not result['reasons'] and reach is not None
    return result


def propose(rng, task, index):
    # Stratify depth and lateral spread; semantic families remain reproducible.
    depth_bin, spread_bin = index % 3, (index//3) % 3
    x_edges = task['depth_bin_edges_m']
    x = rng.uniform(x_edges[depth_bin], x_edges[depth_bin+1])
    spread = rng.uniform(*task['spread_ranges_m'][spread_bin])
    centre_y = rng.uniform(*task['target_y_range_m'])
    skew = rng.uniform(-.035,.035)
    # The target must lie in the shared two-arm workspace; sources need only
    # their assigned arm. Separating these constraints substantially widens coverage.
    p = np.array([[x+skew, centre_y+spread, task['table_z']],
                  [rng.uniform(*task['target_x_range_m']), centre_y, task['table_z']],
                  [x-skew, centre_y-spread, task['table_z']]])
    return p, dict(depth_bin=depth_bin, spread_bin=spread_bin, family=['compact','medium','wide'][spread_bin])
