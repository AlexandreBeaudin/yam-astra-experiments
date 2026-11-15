"""Local collection guidance UI; recorded frame by default, optional live JPEG file.

The live producer must publish atomic JPEG replacements in the SAME calibrated
640x480 collected-image orientation. This server never opens robot/camera devices.
"""
import argparse, hashlib, json, sys, time, uuid
from pathlib import Path
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from threading import Lock
from urllib.parse import urlparse
import cv2, numpy as np
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT/'src'))
from real2sim.layouts import Camera, XtrainerReachability, validate

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run', type=Path, default=ROOT/'runs/stack_cups_distribution')
    p.add_argument('--port', type=int, default=8765)
    p.add_argument('--live-jpeg', type=Path)
    p.add_argument('--synthetic-test-frame', type=Path, help='Explicitly labelled synthetic input, for acceptance-path tests only')
    p.add_argument('--camera-config-sha256', help='Required with live JPEG: acknowledged hash shown by /api/state')
    args = p.parse_args()
    if args.live_jpeg and args.synthetic_test_frame: p.error('Choose live or synthetic input, not both')
    mode = 'live_jpeg' if args.live_jpeg else ('synthetic_test' if args.synthetic_test_frame else 'recorded_reference')
    data = json.loads((args.run/'layouts.json').read_text())
    camera_hash = hashlib.sha256(json.dumps(data['camera'], sort_keys=True).encode()).hexdigest()
    if args.live_jpeg and args.camera_config_sha256 != camera_hash:
        p.error('Live input requires matching --camera-config-sha256 from /api/state; camera identity/orientation must be checked by operator')
    camera = Camera(data['camera'], data['camera']['world_T_camera'])
    reach = XtrainerReachability(ROOT)
    lock = Lock()
    observations = {}
    session = uuid.uuid4().hex
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw): super().__init__(*a, directory=str(args.run), **kw)
        def reply(self, value, status=200):
            raw = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
            self.send_response(status); self.send_header('Content-Type', 'application/json; charset=utf-8'); self.send_header('Cache-Control', 'no-store'); self.end_headers(); self.wfile.write(raw)
        def do_GET(self):
            path = urlparse(self.path).path
            if path == '/api/state':
                return self.reply(dict(**data, camera_config_sha256=camera_hash, input_mode=mode, session_id=session))
            if path == '/api/coverage':
                accepted_by_mode = dict(live_jpeg=0, recorded_reference=0, synthetic_test=0)
                live_layout_ids = set(); live_bins = [0]*9
                by_id = {l['id']: l for l in data['layouts']}
                for record in (args.run/'audit').glob('*.json'):
                    entry = json.loads(record.read_text())
                    # Do not merge audits produced with another asset/calibration generation.
                    if entry.get('camera_config_sha256') != camera_hash or entry.get('assets_sha256') != data['assets_sha256']: continue
                    if entry.get('accepted'):
                        m = entry['input_mode']; accepted_by_mode[m] = accepted_by_mode.get(m, 0)+1
                        if m == 'live_jpeg': live_layout_ids.add(entry['layout_id'])
                for lid in live_layout_ids:
                    tags = by_id.get(lid, {}).get('tags', {})
                    if 'depth_bin' in tags: live_bins[tags['depth_bin']+3*tags['spread_bin']] += 1
                return self.reply(dict(accepted_records_by_mode=accepted_by_mode, unique_live_layouts=len(live_layout_ids),
                                       live_verified_target_bins=live_bins,
                                       note='Manual placement confirmations only; these counts do not assert trajectories were collected'))
            if path == '/api/frame':
                source = args.live_jpeg or args.synthetic_test_frame or args.run/'reference.jpg'
                try:
                    raw = source.read_bytes()
                    frame = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
                    if frame is None or frame.shape[:2] != (480,640): raise ValueError('Camera frame must be 640x480')
                    age = time.time()-source.stat().st_mtime
                    if args.live_jpeg and age > 2: raise ValueError('Live camera frame stale (>2 seconds)')
                    token = hashlib.sha256(raw).hexdigest()
                    observations[token] = dict(seen_at=time.time(), raw=raw)
                    # Bounded cache; keep a frozen displayed frame available for manual annotation.
                    for key in list(observations)[:-20]: observations.pop(key, None)
                    self.send_response(200); self.send_header('Content-Type', 'image/jpeg'); self.send_header('Cache-Control','no-store'); self.send_header('X-Frame-Id', token); self.end_headers(); self.wfile.write(raw)
                except (OSError, ValueError) as e: self.reply(dict(error=str(e)), 503)
                return
            if path.startswith('/api/'): return self.reply(dict(error='not found'),404)
            return super().do_GET()
        def do_POST(self):
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size < 100000: raise ValueError('Invalid body size')
                body = json.loads(self.rfile.read(size))
                if self.path == '/api/validate':
                    with lock: result = validate(body['positions_m'], data['task'], camera, reach)
                    return self.reply(dict(validation=result, render_pending=True, guidance_ready=False,
                                           note='Manual/external candidate requires render_layouts.py before collection guidance'))
                if self.path == '/api/placement':
                    layout = next(x for x in data['layouts'] if x['id'] == body['layout_id'])
                    if not layout.get('guidance_ready'): raise ValueError('Layout has not passed render and settling gates')
                    pixels = np.asarray(body['rim_pixels'], float)
                    if pixels.shape != (3,2) or not np.isfinite(pixels).all() or np.any(pixels<0) or np.any(pixels>[639,479]): raise ValueError('Three ordered rim centres inside image required')
                    snapshot = observations.get(body.get('frame_id'))
                    if snapshot is None: raise ValueError('Unknown/expired displayed frame: refresh and annotate')
                    age = time.time()-snapshot['seen_at']
                    if args.live_jpeg and age > 60: raise ValueError('Annotation frame older than 60 seconds')
                    actual = camera.backproject(pixels, data['task']['table_z']+data['task']['cup']['height'])
                    actual[:,2] = data['task']['table_z']
                    errors = np.linalg.norm(actual[:,:2]-np.array(layout['positions_m'])[:,:2], axis=1)
                    with lock: actual_check = validate(actual, data['task'], camera, reach)
                    accepted = bool(np.all(errors <= .015) and actual_check['passed'])
                    entry = dict(id=uuid.uuid4().hex, time_unix=time.time(), session_id=session,
                                 layout_id=layout['id'], planned_positions_m=layout['positions_m'],
                                 observed_positions_m=actual.tolist(), error_xy_m=errors.tolist(),
                                 accepted=accepted, tolerance_m=.015, input_mode=mode,
                                 method='manual_rim_centre_backprojection_assuming_upright_cup_and_estimated_height',
                                 camera_config_sha256=camera_hash, assets_sha256=data['assets_sha256'],
                                 frame_sha256=body['frame_id'], actual_validation=actual_check,
                                 limitations='No depth or automatic pose verification; 15 mm is a provisional guidance tolerance, not insertion accuracy')
                    audit = args.run/'audit'; audit.mkdir(exist_ok=True)
                    # Persist raw bytes separately, never the overlay pixels.
                    (audit/(entry['id']+'.jpg')).write_bytes(snapshot['raw'])
                    (audit/(entry['id']+'.json')).write_text(json.dumps(entry, ensure_ascii=False, indent=2))
                    return self.reply(entry)
                return self.reply(dict(error='not found'),404)
            except (KeyError, ValueError, TypeError, StopIteration, OSError) as e:
                return self.reply(dict(error=str(e)),400)
    print(f'http://127.0.0.1:{args.port} mode={mode} camera_hash={camera_hash}', flush=True)
    ThreadingHTTPServer(('127.0.0.1', args.port), Handler).serve_forever()

if __name__ == '__main__': main()
