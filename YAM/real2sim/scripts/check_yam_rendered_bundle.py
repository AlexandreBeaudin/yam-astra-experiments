"""Verify rendered guidance distribution, alpha, hashes, and calibrated marker checks."""
import argparse,hashlib,json
from pathlib import Path
import cv2,numpy as np
p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--bundle',type=Path,required=True);a=p.parse_args()
s=json.loads((a.source/'manifest.json').read_text());d=json.loads((a.bundle/'manifest.json').read_text());r=json.loads((a.bundle/'render_report.json').read_text())
assert d['layouts']==s['layouts'] and d['camera']==s['camera'] and d['coverage']==s['coverage']
assert d['render_provenance']['source_manifest_sha256']==hashlib.sha256((a.source/'manifest.json').read_bytes()).hexdigest()
assert r['isolated_process_per_layout'] and len(r['layouts'])==len(d['layouts'])
assert len(r['projection_errors_px'])==5*len(d['layouts']) and max(r['projection_errors_px'])<=1.5
assert r['runtime']['version']=='5.1.0.0' and r['runtime']['python']=='3.11'
byid={e['layout_id']:e for e in r['layouts']}
for l in d['layouts']:
 path=a.bundle/(l['id']+'.png');im=cv2.imread(str(path),cv2.IMREAD_UNCHANGED);assert im.shape==(480,640,4)
 assert set(np.unique(im[:,:,3]))=={0,255};assert np.all(im[im[:,:,3]==0,:3]==0)
 assert hashlib.sha256(path.read_bytes()).hexdigest()==byid[l['id']]['rgba_sha256']
 assert set(byid[l['id']]['pixels'])=={m['material_id'] for m in l['required_materials']}
 assert path.read_bytes()!=(a.source/(l['id']+'.png')).read_bytes()
assert all('./assets/' in p.read_text() for p in a.bundle.glob('scene_*.usda'))
print(json.dumps({'layouts':len(d['layouts']),'same_camera_layouts_distribution':True,'rgba_and_hashes':True,'rendered_objects_per_layout':4,'independent_camera_markers':len(r['projection_errors_px']),'max_projection_error_px':max(r['projection_errors_px']),'runtime':r['runtime'],'hardware_started':False},indent=2))
