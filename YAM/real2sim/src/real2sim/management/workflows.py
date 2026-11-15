"""Versioned edit/render/publish jobs, isolated from collector and Isaac environments."""
import json
from pathlib import Path
import subprocess
import threading
import time
import uuid
from ..releases import digest, file_sha, publish

ROOT = Path(__file__).resolve().parents[3]


class Workflows:
    def __init__(self, catalog, config_path=None):
        self.catalog = catalog
        self.config_path = Path(config_path or ROOT/'configs/manager.local.json').resolve()
        self.lock = threading.Lock()
        self.jobs = {}
        self.hash_cache = {}

    def config(self):
        cfg = json.loads(self.config_path.read_text())
        for key in ('registry','utility_python','scene','station','model'):
            if key in cfg:
                p=Path(cfg[key]);cfg[key]=str(p if p.is_absolute() else ROOT/p)
        return cfg

    def bundle(self, ident):
        doc=next((d for d in self.catalog.records('bundle') if d['id']==ident),None)
        if doc is None: raise ValueError('Unknown bundle')
        if doc['availability']!='catalogued_not_approved': raise ValueError('Source unavailable, changed or recalled')
        cfg=self.config()
        if doc['platform_id']!=cfg['platform'] or doc['task']['task_id'] not in cfg.get('task_ids',[cfg['task_id']]):
            raise ValueError('This station editor is not configured for the selected platform/task')
        if doc.get('station_id') and doc['station_id']!=cfg['station_id']: raise ValueError('Wrong station')
        baseline=Path(cfg['calibration_bundle'])
        if not baseline.is_absolute():baseline=ROOT/baseline
        camera=json.loads((baseline/'manifest.json').read_text())['camera']
        if digest(doc['camera'])!=digest(camera):raise ValueError('Calibration mismatch')
        return doc

    def cached_sha(self, path):
        path=Path(path);stat=path.stat();key=(str(path),stat.st_mtime_ns,stat.st_size)
        if key not in self.hash_cache:self.hash_cache[key]=file_sha(path)
        return self.hash_cache[key]

    def preview_key(self, doc, layout):
        cfg=self.config()
        return digest({'layout':layout['layout'],'camera':doc['camera'],
                       'scene':self.cached_sha(cfg['scene']),
                       'renderer':self.cached_sha(ROOT/'scripts/render_manager_preview.py')})

    def preview(self, doc, layout):
        try:
            if doc['task'].get('adapter')=='mortar_pestle':return self.task3_preview(doc,layout)
            key=self.preview_key(doc,layout)
            path=self.catalog.root/'previews'/(key+'.json')
            ref=json.loads(path.read_text());folder=Path(ref['directory']);r=json.loads((folder/'report.json').read_text())
            if r['layout_sha256']!=digest(layout['layout']) or r['camera_hash']!=digest(doc['camera']):return None
            cfg=self.config()
            if r['scene_sha256']!=self.cached_sha(cfg['scene']) or r['renderer_sha256']!=self.cached_sha(ROOT/'scripts/render_manager_preview.py'):return None
            images={}
            for name,v in r['views'].items():
                p=folder/v['file']
                if p.parent!=folder or file_sha(p)!=v['sha256']:return None
                images[name]=self.catalog.put_image(p.read_bytes())
            return {'images':images,'report':r}
        except (OSError,ValueError,KeyError):return None

    def task3_preview(self, doc, layout):
        """Read-only task review; never reuse the goal-scoring renderer."""
        folder=Path(doc['source'])/'full_scene'
        report=json.loads((folder/'report.json').read_text())
        if report['manifest_sha256']!=doc['source_manifest_sha256']:return None
        if report['renderer_sha256']!=self.cached_sha(ROOT/'scripts/render_mortar_pestle_review.py'):return None
        if report['scene_sha256']!=self.cached_sha(ROOT/'runs/stack_cups_contact/initial.usda'):return None
        row=next((r for r in report['layouts'] if r['id']==layout['id']),None)
        if row is None or row['layout_sha256']!=digest(layout['layout']):return None
        images={}
        for name,item in row['views'].items():
            path=folder/item['file']
            if path.parent!=folder or file_sha(path)!=item['sha256']:return None
            images[name]=self.catalog.put_image(path.read_bytes())
        return {'images':images,'report':dict(row,runtime=report['runtime'],scope=report['scope'],view_visibility_warnings=[view+': '+label+' 可见像素不足，可能被遮挡' for view,v in row['views'].items() for label in v['visibility_warnings']])}

    def status(self):
        with self.lock:return [dict(j) for j in self.jobs.values()]

    def start(self, operation, body):
        if operation not in ('revise','render','render-all','publish','generate'):raise ValueError('Unknown operation')
        doc=self.bundle(body['bundle_id'])
        if doc['task'].get('adapter')=='mortar_pestle' and operation!='publish':raise ValueError('task3 当前支持审阅与发布；修改布局后请离线重新生成')
        layout=next((l for l in doc['layouts'] if l['id']==body.get('layout_id')),None)
        if operation in ('revise','render') and layout is None:raise ValueError('Unknown layout')
        with self.lock:
            if any(j['status'] in ('queued','running') for j in self.jobs.values()):raise ValueError('已有任务执行中，请稍后再试')
            ident=uuid.uuid4().hex
            job={'id':ident,'operation':operation,'status':'queued','created_at':time.time()}
            self.jobs[ident]=job
        threading.Thread(target=self.run,args=(ident,operation,doc,layout,body),daemon=True).start()
        return dict(job)

    def run(self, ident, operation, doc, layout, body):
        job=self.jobs[ident];folder=self.catalog.root/'jobs'/ident;folder.mkdir(parents=True)
        try:
            job['status']='running';(folder/'job.json').write_text(json.dumps(job,ensure_ascii=False));cfg=self.config();source=Path(doc['source'])
            if file_sha(source/doc['manifest_name'])!=doc['source_manifest_sha256']:raise ValueError('Source changed')
            if operation=='generate':
                if doc['platform_id']!='xtrainer' or doc['task'].get('adapter')!='goal_scoring':raise ValueError('This station has no configured generator for the selected task')
                output=self.catalog.root/'drafts'/ident
                self.execute([cfg['utility_python'],str(ROOT/'scripts/prepare_goal_supervision.py'),'--source-bundle',str(source),'--output',str(output),'--seed',str(int(time.time()))],folder)
                result=self.catalog.import_bundle(output,doc['platform_id'],cfg['station_id'])
                job['result']={'bundle_id':result['id'],'source':str(output),'passed':True}
            elif operation=='revise':
                request={k:body[k] for k in ('layout_id','changes','exclude') if k in body}
                request['source_sha256']=doc['source_manifest_sha256']
                (folder/'request.json').write_text(json.dumps(request,allow_nan=False))
                (folder/'config.json').write_text(json.dumps(cfg))
                output=self.catalog.root/'drafts'/ident
                command=[cfg['utility_python'],str(ROOT/'scripts/revise_guidance.py'),'--source',str(source),'--output',str(output),'--request',str(folder/'request.json'),'--config',str(folder/'config.json')]
                self.execute(command,folder)
                result=self.catalog.import_bundle(output,doc['platform_id'],cfg['station_id'])
                audit_report=json.loads((output/'manifest.json').read_text()).get('distribution_audit',{})
                job['result']={'bundle_id':result['id'],'source':str(output),'passed':all(l['layout']['validation']['passed'] for l in result['layouts']) and audit_report.get('passed') is not False}
            elif operation in ('render','render-all'):
                if doc['platform_id']!='xtrainer':raise ValueError('YAM full-scene adapter is not installed on this station')
                targets=doc['layouts'] if operation=='render-all' else [layout]
                for index, current in enumerate(targets):
                    job['progress']=f'{index}/{len(targets)}'
                    if self.preview(doc,current) is not None:continue
                    output=self.catalog.root/'previews'/(ident+'_'+current['id'])
                    logdir=folder/current['id'];logdir.mkdir()
                    self.execute(['bash',str(ROOT/'scripts/isaac_python.sh'),str(ROOT/'scripts/render_manager_preview.py'),'--source',str(source),'--layout',current['id'],'--output',str(output),'--scene',cfg['scene']],logdir)
                    r=json.loads((output/'report.json').read_text())
                    if r['layout_sha256']!=digest(current['layout']):raise ValueError('Render changed layout')
                    key=self.preview_key(doc,current);target=output.parent/(key+'.json')
                    tmp=target.with_suffix('.tmp');tmp.write_text(json.dumps({'directory':str(output)}));tmp.replace(target)
                job['progress']=f'{len(targets)}/{len(targets)}'
                job['result']={'bundle_id':doc['id']}
            else:
                data=json.loads((source/'manifest.json').read_text())
                required={l['id'] for l in doc['layouts']} if doc['task'].get('adapter')=='mortar_pestle' else (set(data.get('manager_required_previews',[])) or {doc['layouts'][0]['id']})
                for l in doc['layouts']:
                    if l['id'] in required and self.preview(doc,l) is None:raise ValueError('请先渲染当前版本的全场景预览: '+l['id'])
                note=body.get('note','')
                if not isinstance(note,str) or len(note)>1000:raise ValueError('Invalid release note')
                result=publish(cfg['registry'],source,cfg['station_id'],note)
                job['result']={'release_id':result['id'],'source':result['source'],'bundle_id':doc['id']}
            job['status']='done'
        except Exception as error:
            job['status']='failed';job['error']=str(error)
        finally:
            job['finished_at']=time.time();(folder/'job.json').write_text(json.dumps(job,ensure_ascii=False,indent=2))

    @staticmethod
    def execute(command,folder):
        import os
        env=os.environ.copy();env['PYTHONPATH']=str(ROOT/'src')
        with (folder/'output.log').open('w') as log:
            p=subprocess.run(command,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=900)
        if p.returncode:
            # Retain full diagnostic log locally. Only return a bounded tail.
            tail=(folder/'output.log').read_text(errors='replace')[-2000:]
            raise ValueError('执行失败，日志 '+str(folder/'output.log')+'\n'+tail)

    def enrich(self, overview):
        from .geometry import goal_envelopes
        from ..distribution import audit
        for doc in overview['bundles']:
            data={'layouts':[l['layout'] for l in doc['layouts']],'task':doc['task']}
            if doc['availability']=='catalogued_not_approved':
                try:
                    source=json.loads((Path(doc['source'])/doc['manifest_name']).read_text())
                    data.update({k:source[k] for k in ('split_policy','required_materials') if k in source})
                    doc['generation_config']=source.get('config',{})
                except (OSError,ValueError):pass
            doc['distribution_audit']=audit(data)
            for layout in doc['layouts']:
                if not layout['geometry']:layout['geometry']=goal_envelopes(layout['layout'],doc['task'])
                layout['scene_preview']=self.preview(doc,layout)
        overview['jobs']=self.status()
        try:
            from ..releases import latest
            cfg=self.config();overview['station']={k:cfg[k] for k in ('station_id','platform','task_id')}
            base=Path(cfg['calibration_bundle']);base=base if base.is_absolute() else ROOT/base
            camera=json.loads((base/'manifest.json').read_text())['camera']
            overview['release_selections']={tid:latest(cfg['registry'],cfg['platform'],tid,cfg['station_id'],digest(camera)) for tid in cfg.get('task_ids',[cfg['task_id']])}
            overview['release_selection']=overview['release_selections'].get(cfg['task_id'],{})
        except (OSError,ValueError,KeyError):overview['station']=None
        return overview
