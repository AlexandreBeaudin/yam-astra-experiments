"""Inspect Feishu column names or synchronize a reviewed metadata snapshot."""
import argparse
import hashlib
import json
import os
import sys
import tempfile
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from real2sim.feishu import Client, FeishuError, normalize

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',type=Path,default=ROOT/'configs/feishu_materials.local.json')
    p.add_argument('--inspect',action='store_true',help='Read column names/IDs only; do not synchronize records')
    p.add_argument('--output',type=Path,default=ROOT/'runs/feishu_materials')
    a=p.parse_args()
    try:
        cfg=json.loads(a.config.read_text())
        client=Client();client.authenticate()
        if cfg.get('wiki_node_token'):
            node=client.request('/wiki/v2/spaces/get_node?'+urllib.parse.urlencode({'token':cfg['wiki_node_token']}))['data']['node']
            if node['obj_type'] != 'bitable':
                raise ValueError('This wiki node is not a Bitable; provide the material table node link')
            cfg['app_token']=node['obj_token']
        if not cfg.get('app_token') or cfg['app_token'].startswith('REPLACE_'):
            raise ValueError('Configure app_token or wiki_node_token first')
        if not cfg.get('table_id') or cfg['table_id'].startswith('REPLACE_'):
            tables=client.list_all(cfg['app_token'],'','tables')
            if len(tables)!=1:
                print(json.dumps([{'table_id':t['table_id'],'name':t['name']} for t in tables],ensure_ascii=False,indent=2))
                raise ValueError('Select the material table_id in the local config')
            cfg['table_id']=tables[0]['table_id']
        schema=client.list_all(cfg['app_token'],cfg['table_id'],'fields')
        if a.inspect:
            print(json.dumps([{'name': f['field_name'],'field_id':f['field_id'],'type':f['type']} for f in schema],ensure_ascii=False,indent=2));return
        records=client.list_all(cfg['app_token'],cfg['table_id'],'records')
        materials,absent=normalize(records,cfg['fields'],schema)
        snapshot=dict(schema_version='1.0',source='feishu_bitable',synced_at=datetime.now(timezone.utc).isoformat(),
                      app_token=cfg['app_token'],table_id=cfg['table_id'],field_mapping=cfg['fields'],
                      absent_optional_columns=absent,materials=materials,
                      scope='Metadata staging only; assets are not downloaded or promoted and task registry is unchanged')
        raw=json.dumps(snapshot,ensure_ascii=False,indent=2,allow_nan=False).encode()
        digest=hashlib.sha256(raw).hexdigest()
        a.output.mkdir(parents=True,exist_ok=True)
        # An immutable snapshot supports reproducibility; latest is replaced only after full validation.
        (a.output/(digest+'.json')).write_bytes(raw)
        fd,tmp=tempfile.mkstemp(dir=a.output,prefix='.latest-')
        try:
            with os.fdopen(fd,'wb') as f:f.write(raw)
            os.replace(tmp,a.output/'latest.json')
        finally:
            if os.path.exists(tmp):os.unlink(tmp)
        print(json.dumps({'materials':len(materials),'snapshot':str(a.output/'latest.json'),
                          'sha256':digest,'absent_optional_columns':absent,'simulation_registry_updated':False},ensure_ascii=False,indent=2))
    except (FeishuError,ValueError,OSError,KeyError) as e:
        p.exit(1,str(e)+'\n')

if __name__=='__main__':main()
