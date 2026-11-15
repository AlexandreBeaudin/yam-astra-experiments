"""YAM task links -> photo-inspected recipes -> simulation candidates -> BOS/Feishu."""
import argparse
import hashlib
import html
import json
import re
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from real2sim.feishu import Client, API, normalize, text_value
from real2sim.reconstruction.tasks import select_materials
from real2sim.storage.bos import digest_file


def save(path, value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')


def sync(args):
    from PIL import Image
    cfg=json.loads(args.config.read_text());client=Client();client.authenticate()
    tables=client.list_all(cfg['app_token'],'','tables')
    matches=[t for t in tables if t['name']=='YAM 数采记录']
    if len(matches)!=1:raise ValueError('YAM task table name is not unique')
    task_table=matches[0]['table_id']
    schema=client.list_all(cfg['app_token'],cfg['table_id'],'fields')
    records=client.list_all(cfg['app_token'],cfg['table_id'],'records')
    materials,_=normalize(records,cfg['fields'],schema)
    tasks=client.list_all(cfg['app_token'],task_table,'records')
    selection=select_materials(tasks,materials,cfg['table_id'])
    args.run.mkdir(parents=True,exist_ok=True)
    source=args.run/'source.json'
    if source.exists():raise ValueError('Pinned source exists; use a new run directory for a new snapshot')
    selection['task_table_id']=task_table;selection['material_table_id']=cfg['table_id']
    selection['schema']='real2sim.yam-linked-materials/v1'
    (args.run/'input').mkdir(exist_ok=True)
    for m in selection['materials']:
        if not m['photos']:raise ValueError('Material photo missing: '+m['material_id'])
        att=m['photos'][0]
        req=urllib.request.Request(API+'/drive/v1/medias/'+att['file_token']+'/download',headers={'Authorization':'Bearer '+client.token})
        try:
            with client.opener.open(req,timeout=30) as response:raw=response.read()
        except Exception as error:raise RuntimeError('Photo download failed: '+type(error).__name__) from None
        if len(raw)!=att['size']:raise ValueError('Photo size mismatch')
        path=args.run/'input'/(m['material_id']+'.jpg');path.write_bytes(raw)
        with Image.open(path) as im:im.verify()
        m['photo_sha256']=digest_file(path)
    save(source,selection)
    print(json.dumps({'linked_tasks':len(selection['tasks']),'materials':len(selection['materials']),'skipped':selection['skipped_tasks']},ensure_ascii=False),flush=True)


def build(args):
    from PIL import Image, ImageOps, ImageDraw
    from real2sim.reconstruction.materials import geometry,validate_geometry
    from real2sim.reconstruction.export import export_asset
    from real2sim.reconstruction.validate import validate_and_render
    source=json.loads((args.run/'source.json').read_text());recipes=json.loads(args.recipes.read_text())
    specs={s['id']:s for s in recipes['items']}
    missing={m['material_id'] for m in source['materials']}-set(specs)
    if missing:raise ValueError('Photo-inspected recipes missing: '+str(sorted(missing)))
    if (args.run/'batch.json').exists():raise ValueError('Batch already built; use the pinned batch for publication')
    versions_path=args.run/'versions.json'
    if versions_path.exists():versions=json.loads(versions_path.read_text())
    else:
        versions={}
        for m in source['materials']:
            parent=ROOT/'assets/materials'/m['material_id'];existing=[p.name for p in parent.glob('v*')]+[m.get('asset_version','')]
            numbers=[int(v[1:]) for v in existing if re.fullmatch(r'v\d+',v)]
            versions[m['material_id']]=f'v{max(numbers,default=0)+1:03d}'
        save(versions_path,versions)
    entries=[];cards=[];board=Image.new('RGB',(1600,len(source['materials'])*290),(238,240,242));draw=ImageDraw.Draw(board)
    for row,m in enumerate(source['materials']):
        mid=m['material_id'];spec=specs[mid];version=versions[mid]
        if m['key_dimensions'].strip()!=spec['expected_dimensions_text'].strip():raise ValueError('Dimensions changed: '+mid)
        photo=args.run/'input'/(mid+'.jpg')
        if digest_file(photo)!=m['photo_sha256']:raise ValueError('Source photo changed')
        parent=ROOT/'assets/materials'/mid;parent.mkdir(parents=True,exist_ok=True);dest=parent/version
        recipe_sha=hashlib.sha256(json.dumps(spec,sort_keys=True).encode()).hexdigest()
        if dest.exists():
            manifest=json.loads((dest/'manifest.json').read_text())
            if manifest['source_snapshot_sha256']!=digest_file(args.run/'source.json') or manifest['recipe_sha256']!=recipe_sha:
                raise ValueError('Immutable version exists with different source or recipe')
        else:
            visuals,colliders=geometry(spec);check=validate_geometry(spec,visuals,colliders)
            if not check['passed']:raise ValueError('Geometry failed: '+mid)
            with tempfile.TemporaryDirectory(prefix='.'+version+'-',dir=parent) as temp:
                out=Path(temp);export_asset(out,spec,visuals,colliders)
                physics=validate_and_render(out,spec,visuals)
                if not physics['passed']:save(args.run/(mid+'_failed_validation.json'),physics);raise ValueError('Settling failed: '+mid)
                (out/'evidence').mkdir();shutil.copyfile(photo,out/'evidence/source.jpg')
                save(out/'recipe.json',spec)
                evidence={k:m.get(k) for k in ['material_id','name','source_record_id','key_dimensions','dimensions_source','photo_sha256','quantity']}
                evidence['quantity_scope']='inventory only; task usage unknown'
                save(out/'evidence/source.json',evidence)
                save(out/'validation.json',{'geometry':check,'static_physics':physics,'visual_review':'candidate requires source appearance and calibrated task-scene review'})
                tasks=[t['task_id'] for t in source['tasks'] if any(x['material_id']==mid for x in t['materials'])]
                manifest={'schema':'real2sim.material/v1','material_id':mid,'source_record_id':m['source_record_id'],'name':m['name'],
                    'version':version,'task_ids':tasks,'source_dimensions':m['key_dimensions'],'dimensions_source':m['dimensions_source'],
                    'dimensions_m':[v/1000 for v in spec['dimensions_mm']],'units':'m','up_axis':'z',
                    'source_snapshot_sha256':digest_file(args.run/'source.json'),'photo_sha256':m['photo_sha256'],'recipe_sha256':recipe_sha,
                    'builder_sources':{str(p.relative_to(ROOT)):digest_file(p) for p in [Path(__file__),*sorted((ROOT/'src/real2sim/reconstruction').glob('*.py'))]},
                    'method':recipes['method'],'asset_usd':'asset.usda','visual_glb':'visual.glb','mujoco':'asset.mjcf.xml',
                    'mesh_validation':check,'static_physics':physics,'quality':'simulation_candidate_pending_review',
                    'guidance_ready':False,'contact_validated':False,'geometry_candidate_usable':True,
                    'limitations':spec['assumptions']+['Mass/inertia/friction are assumed; no real robot grasp or calibrated scene acceptance.',
                     'Single photo and registered dimensions; no multiview reconstruction, depth fusion or photo texture recovery.']}
                save(out/'manifest.json',manifest)
                # Finalize the version only after all build/check/render steps succeed.
                if dest.exists():raise ValueError('Version appeared concurrently')
                out.rename(dest)
        # Pin every byte that may later be published, independently of the mutable local directory.
        from real2sim.storage.bos import plan_bundle
        entries.append({'material_id':mid,'version':version,'directory':str(dest),
                        'bundle_id':plan_bundle(dest)['bundle_id'],'manifest':manifest})
        photo_im=ImageOps.exif_transpose(Image.open(photo)).convert('RGB').crop(spec['photo_roi'])
        crop=args.run/(mid+'_source_crop.jpg');photo_im.save(crop)
        draw.text((12,row*290+5),mid+' / '+version+' | geometry + MuJoCo settling passed',fill=(20,30,40))
        for col,im in enumerate([photo_im,*[Image.open(dest/(v+'.png')).convert('RGB') for v in ['oblique','front','top']]]):
            im.thumbnail((390,245));board.paste(im,(col*400+(400-im.width)//2,row*290+30+(245-im.height)//2))
        figures=''.join('<figure><img src="'+html.escape(str(p.relative_to(ROOT)),quote=True)+'"><figcaption>'+label+'</figcaption></figure>' for p,label in [(crop,'实物照片裁切'),(dest/'oblique.png','MuJoCo 斜视'),(dest/'front.png','正视'),(dest/'top.png','顶视')])
        cards.append('<article><h2>'+html.escape(mid+' '+m['name']+' '+version)+'</h2><p>'+html.escape(m['key_dimensions'])+'</p><div>'+figures+'</div><ul>'+''.join('<li>'+html.escape(s)+'</li>' for s in manifest['limitations'])+'</ul></article>')
        print(mid,version,'geometry + 3 settling trials + 3 renders passed',flush=True)
    board.save(args.run/'overview.jpg')
    save(args.run/'batch.json',{'assets':entries,'task_mapping':source['tasks'],'skipped_tasks':source['skipped_tasks'],
        'backend':'MuJoCo','simfoundry_executed':False,'physx_executed':False,'source_sha256':digest_file(args.run/'source.json')})
    # Base URL resolves all project-relative asset references when opening the local page.
    page='<!doctype html><html lang="zh"><meta charset="utf-8"><base href="../../"><title>YAM 关联物料重建</title><style>body{font:16px system-ui;background:#eef0f2;margin:24px}article{background:white;padding:20px;margin:20px 0}article div{display:flex}figure{width:24%;margin:5px}img{width:100%;height:260px;object-fit:contain}li{margin:8px}</style><h1>YAM 关联物料：照片与仿真候选对照</h1><p>6 件候选 · 18 次 MuJoCo 静置 · 18 张渲染。质量和摩擦为假设；照片未与渲染配准，不作为像素误差验收。尚未验证抓取与任务布局。</p>'+''.join(cards)
    (args.run/'index.html').write_text(page)


def publish(args):
    from real2sim.storage.bos import upload_bundle,plan_bundle
    from real2sim.storage.feishu_writeback import writeback
    batch=json.loads((args.run/'batch.json').read_text());source=json.loads((args.run/'source.json').read_text())
    if batch['source_sha256']!=digest_file(args.run/'source.json'):raise ValueError('Pinned task source changed')
    cfg=json.loads(args.config.read_text());bos=json.loads(args.bos_config.read_text())
    client=Client();client.authenticate()
    materials,_=normalize(client.list_all(cfg['app_token'],cfg['table_id'],'records'),cfg['fields'],client.list_all(cfg['app_token'],cfg['table_id'],'fields'))
    current=select_materials(client.list_all(cfg['app_token'],source['task_table_id'],'records'),materials,cfg['table_id'])
    current_links={t['task_id']:{(m['material_id'],m['source_record_id']) for m in t['materials']} for t in current['tasks']}
    for t in source['tasks']:
        if current_links.get(t['task_id'])!={(m['material_id'],m['source_record_id']) for m in t['materials']}:raise ValueError('Task links changed since reconstruction')
    current_materials={m['material_id']:m for m in materials}
    original_materials={m['material_id']:m for m in source['materials']}
    for entry in batch['assets']:
        manifest=entry['manifest'];m=current_materials[entry['material_id']]
        if m['source_record_id']!=manifest['source_record_id'] or m['key_dimensions']!=manifest['source_dimensions']:raise ValueError('Material source changed since reconstruction')
        if plan_bundle(entry['directory'])['bundle_id']!=entry['bundle_id']:raise ValueError('Asset changed after validation')
        old=original_materials[entry['material_id']]
        expected_uri=f"bos://{bos['bucket']}/{bos['prefix'].strip('/')}/{entry['bundle_id']}/files/"
        if (m['asset_uri'],m['asset_version']) not in [(old['asset_uri'],old['asset_version']),(expected_uri,entry['version'])]:
            raise ValueError('Material asset pointer changed concurrently; refusing stale writeback')
        if m['photos']!=old['photos']:raise ValueError('Material photo source changed since reconstruction')
    receipts=args.run/'uploads';receipts.mkdir(exist_ok=True)
    for entry in batch['assets']:
        path=receipts/(entry['material_id']+'.json')
        result=upload_bundle(entry['directory'],bos)
        save(path,result)
        result['feishu_writeback']={'status':'pending'};save(path,result)
        try:
            result['feishu_writeback']=writeback(result,cfg,client=client)
        except Exception as error:
            result['feishu_writeback']={'status':'failed','error_type':type(error).__name__};save(path,result);raise
        save(path,result)
        if result['feishu_writeback']['status']!='complete':raise ValueError('Material writeback not completed')
        print(entry['material_id'],'BOS complete; Feishu read-back verified',flush=True)
    save(args.run/'publication.json',{'status':'complete','materials':[{ 'material_id':e['material_id'],'version':e['version'],**json.loads((receipts/(e['material_id']+'.json')).read_text())['feishu_writeback']} for e in batch['assets']]})


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['sync','build','publish'])
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--config',type=Path,default=ROOT/'configs/feishu_materials.local.json')
    parser.add_argument('--bos-config',type=Path,default=ROOT/'configs/bos.local.json')
    parser.add_argument('--recipes',type=Path,default=ROOT/'configs/reconstruction/yam_linked_materials.json')
    args=parser.parse_args();args.run=args.run.resolve()
    try:globals()[args.action](args)
    except Exception as error:
        # Do not print signed SDK requests or credential-bearing exceptions.
        message={'error_type':type(error).__name__}
        if isinstance(error,ValueError):message['message']=str(error)
        print(json.dumps(message,ensure_ascii=False),file=sys.stderr);return 1
    return 0

if __name__=='__main__':raise SystemExit(main())
