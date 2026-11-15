"""Bind a verified layout distribution to a task and a Feishu material snapshot.

Reuse existing geometry/IK/PhysX/RTX evidence without re-running unchanged checks.
"""
import argparse,hashlib,json,shutil,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--profile',type=Path,default=ROOT/'configs/supervision/stack_cups.json')
p.add_argument('--distribution',type=Path,default=ROOT/'runs/stack_cups_distribution')
p.add_argument('--catalog',type=Path,default=ROOT/'runs/feishu_materials/latest.json')
p.add_argument('--output',type=Path,default=ROOT/'runs/stack_cups_supervision')
a=p.parse_args();profile=json.loads(a.profile.read_text());data=json.loads((a.distribution/'layouts.json').read_text());catalog=json.loads(a.catalog.read_text())
if a.output.exists():raise SystemExit('Output exists; use a new output path to preserve supervision audits')
if profile['task_id']!=data['task']['task_id']:raise SystemExit('Task mismatch')
materials={m['material_id']:m for m in catalog['materials']}
for required in profile['required_materials']:
    m=materials.get(required['material_id'])
    if not m or m['name']!=required['expected_name']:raise SystemExit('Required material missing or renamed: '+required['material_id'])
    if required['asset_id']!=data['task']['asset_id'] or required['asset_version']!=data['task']['asset_version']:raise SystemExit('Asset binding mismatch')
    registry=ROOT/required['asset_registry']
    if data['assets_sha256'].get(required['asset_registry'])!=hashlib.sha256(registry.read_bytes()).hexdigest():raise SystemExit('Asset registry changed; rebuild layout validation')
if list(profile['role_bindings'])!=data['task']['roles']:raise SystemExit('Task role ordering mismatch')
for source,digest in data['assets_sha256'].items():
    if hashlib.sha256((ROOT/source).read_bytes()).hexdigest()!=digest:raise SystemExit('Validation inputs changed: '+source)
ready=[l for l in data['layouts'] if l.get('guidance_ready')]
if not ready:raise SystemExit('No usable layouts')
for l in data['layouts']:
    for name in ('top.png','props.png','overlay.png'):
        if not (a.distribution/l['id']/name).is_file():raise SystemExit('Missing render')
a.output.mkdir(parents=True)
for name in ('layouts.json','reference.jpg'):shutil.copy2(a.distribution/name,a.output/name)
render_hashes={}
for l in data['layouts']:
    shutil.copytree(a.distribution/l['id'],a.output/l['id'])
    for name in ('top.png','props.png','overlay.png'):
        path=a.output/l['id']/name;render_hashes[str(path.relative_to(a.output))]=hashlib.sha256(path.read_bytes()).hexdigest()
bundle=dict(schema_version='1.0',profile=profile,materials=catalog['materials'],
            catalog_snapshot_sha256=hashlib.sha256(a.catalog.read_bytes()).hexdigest(),
            layouts_sha256=hashlib.sha256((a.output/'layouts.json').read_bytes()).hexdigest(),render_sha256=render_hashes,
            source_distribution=str(a.distribution.resolve()),catalog_synced_at=catalog['synced_at'])
(a.output/'supervision.json').write_text(json.dumps(bundle,ensure_ascii=False,indent=2))
subprocess.run([str(ROOT/'.venv/bin/python'),str(ROOT/'scripts/build_guidance.py'),'--run',str(a.output)],check=True)
print(json.dumps({'task':profile['task_id'],'usable_layouts':len(ready),'material_ids':[m['material_id'] for m in profile['required_materials']],'output':str(a.output)},ensure_ascii=False))
