"""Read one Feishu YAM task and its linked materials/photos without cloud writes."""
import argparse
import hashlib
import json
import re
from pathlib import Path
import sys
import urllib.parse
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from real2sim.feishu import Client,normalize,text_value


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--task',default='TASK-YAM-0015');p.add_argument('--output',type=Path,required=True)
    p.add_argument('--config',type=Path,default=ROOT/'configs/feishu_materials.local.json');a=p.parse_args()
    if a.output.exists():p.error('Use a new immutable input directory')
    cfg=json.loads(a.config.read_text());c=Client();c.authenticate()
    tables=[t for t in c.list_all(cfg['app_token'],'','tables') if t['name']=='YAM 数采记录']
    if len(tables)!=1:raise ValueError('YAM task table is not unique')
    table=tables[0]['table_id'];tasks=[t for t in c.list_all(cfg['app_token'],table,'records') if text_value(t['fields'].get('编号'))==a.task]
    if len(tasks)!=1:raise ValueError('Task must exist exactly once')
    task=tasks[0];ids=set()
    for link in task['fields'].get('物料',[]):
        if link.get('record_ids') and link.get('table_id')!=cfg['table_id']:raise ValueError('Unexpected material table')
        ids.update(link.get('record_ids',[]))
    records=[r for r in c.list_all(cfg['app_token'],cfg['table_id'],'records') if r['record_id'] in ids]
    if not ids or {r['record_id'] for r in records}!=ids:raise ValueError('Missing linked records')
    materials,_=normalize(records,cfg['fields'],c.list_all(cfg['app_token'],cfg['table_id'],'fields'))
    if any(not re.fullmatch(r'MAT-\d+',m['material_id']) for m in materials):raise ValueError('Unsafe material identifier')
    byid={r['record_id']:r for r in records};a.output.mkdir(parents=True)
    photo_hashes={}
    for m in materials:
        from PIL import Image
        import io
        att=byid[m['source_record_id']]['fields'][cfg['fields']['photos']][0]
        # Preserve the table/record permission context supplied by Feishu. Bare
        # file_token download can fail for attachments with advanced permissions.
        url=urllib.parse.urlparse(att['tmp_url'])
        if url.scheme!='https' or url.hostname!='open.feishu.cn' or not url.path.startswith('/open-apis/drive/v1/medias/'):raise ValueError('Unexpected media endpoint')
        response=c.request(url.path.removeprefix('/open-apis')+'?'+url.query)
        links=response['data']['tmp_download_urls']
        if len(links)!=1:raise ValueError('No unique authorized attachment URL')
        with c.opener.open(links[0]['tmp_download_url'],timeout=30) as r:raw=r.read()
        if len(raw)!=att['size']:raise ValueError('Photo size mismatch')
        Image.open(io.BytesIO(raw)).verify()
        (a.output/(m['material_id']+'.jpg')).write_bytes(raw)
        photo_hashes[m['material_id']]=hashlib.sha256(raw).hexdigest()
    fields={k:task['fields'].get(k) for k in ['编号','任务名','任务细节','注意事项 & 质检要点','机器编号']}
    result={'task_fields':fields,'tasks':[{'task_id':a.task,'task_record_id':task['record_id'],'task_name':text_value(fields['任务名'])}],
            'task_table_id':table,'material_table_id':cfg['table_id'],'materials':materials,'photo_sha256':photo_hashes}
    # Publish source last; incomplete photo download is not a usable snapshot.
    (a.output/'source.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'task':a.task,'materials':len(materials),'output':str(a.output)},ensure_ascii=False))

if __name__=='__main__':
    try:main()
    except Exception as e:
        print('Task sync failed: '+type(e).__name__,file=sys.stderr);raise SystemExit(1)
