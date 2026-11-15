"""Exercise an actual YAM guidance bundle through native preview and MCAP; no devices."""
import argparse
import base64
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import urllib.request
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from gello.data_utils.real2sim_guidance import session
from gello.data_utils.mcap_recording import McapEpisodeWriter
from gello.data_utils.mcap_tools import messages
from gello.cameras.preview import CameraPreview
from scripts.export_guidance import export


def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--bundle',type=Path,required=True);p.add_argument('--report',type=Path,required=True);a=p.parse_args()
 manifest=json.loads((a.bundle/'manifest.json').read_text());cam=manifest['camera'];camera=dict(serial=cam['serial'],model='D435',width=640,height=480,fps=30,rotation=0)
 config=SimpleNamespace(cameras={'main':SimpleNamespace(**camera)})
 g=session(a.bundle,config);snapshots=[]
 for i in range(len(g.ids)):
  g.select(i);s=g.snapshot();m=s['required_materials'];assert len(m)==4 and m[0]['material_id']=='MAT-00050'
  assert len({x['material_id'] for x in m})==4 and all(x['quantity']==1 for x in m)
  assert s['task_rules']['target_drawer']=='upper' and s['task_rules']['initial_state']=='closed'
  assert hashlib.sha256(base64.b64decode(s['projection_png_base64'])).hexdigest()==s['projection_sha256']
  assert s['layout']['validation']['passed'];snapshots.append(s)
 counts=Counter(x['material_id'] for s in snapshots for x in s['required_materials'][1:]);assert set(counts.values())=={30}
 combos=Counter(tuple(sorted(x['material_id'] for x in s['required_materials'][1:])) for s in snapshots);assert len(combos)==20 and set(combos.values())=={3}
 g.select(0);rig=SimpleNamespace(config=config,latest_preview_frames=lambda:{})
 with tempfile.TemporaryDirectory(prefix='yam15-integration-') as td:
  root=Path(td);preview=CameraPreview(rig,port=0,guidance=g);preview.start()
  try:
   op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
   with op.open(preview.url) as response:page=response.read().decode();assert 'required_materials' in page and 'guide-next' in page
   req=urllib.request.Request(preview.url+'guidance',data=b'{"index":1}',headers={'Content-Type':'application/json','X-YAM-Control':preview.token})
   with op.open(req) as response:assert json.load(response)['required_materials']==snapshots[1]['required_materials']
  finally:preview.close()
  w=McapEpisodeWriter(root/'data','TASK-YAM-0015',200,image_encoding='raw+zstd',camera_metadata={'main':camera});w.guidance=g
  evidence=[]
  try:
   for i,index in enumerate([0,1,1]):
    g.select(index);expected=g.snapshot();w.start();g.select((index+1)%len(g.ids))
    w.append({},np.zeros(14),0,leader_action=np.zeros(14));w.record('/cameras/main/rgb',dict(frame_number=0,serial=camera['serial']),w._epoch,image=np.zeros((480,640,3),np.uint8))
    path=w.commit('fail' if i==1 else 'success');snap=list(messages(path,'/guidance/initial'))[0][2]
    assert snap['layout_id']==expected['layout_id'] and snap['required_materials']==expected['required_materials']
    out=export(path,root/('review'+str(i)));assert (out/'guidance_projection.png').read_bytes()==(a.bundle/(expected['layout_id']+'.png')).read_bytes()
    assert (out/'guidance_overlay_first_frame.jpg').exists()
    evidence.append({'layout_id':snap['layout_id'],'materials':snap['required_materials'],'label':'fail' if i==1 else 'success'})
  finally:w.close()
 report={'bundle_manifest_sha256':hashlib.sha256((a.bundle/'manifest.json').read_bytes()).hexdigest(),'layouts_checked':len(g.ids),'material_counts':dict(counts),'combinations':len(combos),'native_preview_selection':True,'three_episodes_one_writer':True,'snapshots_frozen_at_start':True,'per_episode_materials_preserved':True,'repeat_same_guidance':True,'first_frame_export':True,'episodes':evidence,'hardware_started':False,'image_source':'synthetic blank frame for storage test only; no optical alignment acceptance','test_records':'temporary directory only'}
 a.report.parent.mkdir(parents=True,exist_ok=True);a.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');print(json.dumps({k:v for k,v in report.items() if k!='episodes'},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
