"""Attach versioned live UI and a train-only field-test itinerary; no cloud/devices."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from real2sim.releases import file_sha
p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);a=p.parse_args();run=a.run.resolve();data=json.loads((run/'manifest.json').read_text())
if data['task'].get('adapter')!='mortar_pestle':raise ValueError('Expected mortar/pestle task')
if data.get('render_kind')!='isaac_asset_rgba':raise ValueError('请先用 render_task3_live_assets.py 生成资产渲染版；不再生成新的线框实时指引')
train=[l for l in data['layouts'] if l['split']=='train'];picked=[next(l['id'] for l in train if l['bin']==i) for i in range(9)]
for key in (lambda l:l['pestle_yaw_deg'],lambda l:-l['pestle_yaw_deg'],lambda l:l['bowl_m'][0]):
 picked.append(next(l['id'] for l in sorted(train,key=key) if l['id'] not in picked))
page=(ROOT/'web/goal_guidance.html').read_bytes();import hashlib
meta={'schema_version':1,'task_id':data['task']['task_id'],'manifest_sha256':file_sha(run/'manifest.json'),'index_sha256':hashlib.sha256(page).hexdigest(),'scope':'initial placement guidance for manual teleoperation; not physical task certification','field_test_layout_ids':picked,'field_test_policy':'train only; saved capture_phase=field_test; excluded from formal coverage; do not use held-out validation for debugging'}
outputs={'index.html':page,'live_guidance.json':json.dumps(meta,ensure_ascii=False,indent=2).encode()}
for name,raw in outputs.items():
 path=run/name
 if path.exists() and path.read_bytes()!=raw:raise ValueError('Live artifacts already exist with different content; create a new bundle version')
for name,raw in outputs.items():
 path=run/name
 if not path.exists():path.write_bytes(raw)
print(json.dumps(meta,ensure_ascii=False))
