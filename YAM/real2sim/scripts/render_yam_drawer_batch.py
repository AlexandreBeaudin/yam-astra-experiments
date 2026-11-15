"""Render each guidance layout in an isolated Isaac process, then publish a bundle."""
import argparse,copy,hashlib,html,json,shutil,subprocess,sys
from pathlib import Path
import cv2,numpy as np
ROOT=Path(__file__).resolve().parents[1]

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 a.source=a.source.resolve();a.output=a.output.resolve();work=a.output.with_name(a.output.name+'.building')
 if a.output.exists() or work.exists():p.error('Use a fresh output directory')
 source=json.loads((a.source/'manifest.json').read_text());work.mkdir(parents=True);(work/'logs').mkdir();(work/'frames').mkdir();reports=[];tiles=[];cards=[]
 for i,l in enumerate(source['layouts']):
  run=work/'frames'/l['id'];log=work/'logs'/(l['id']+'.log')
  cmd=['bash',str(ROOT/'scripts/isaac_python.sh'),str(ROOT/'scripts/render_yam_drawer_guidance.py'),'--source',str(a.source),'--output',str(run),'--start',str(i),'--limit','1']
  with log.open('w') as f:subprocess.run(cmd,cwd=ROOT,stdout=f,stderr=subprocess.STDOUT,check=True)
  report=json.loads((run/'render_report.json').read_text());assert len(report['layouts'])==1 and report['layouts'][0]['layout_id']==l['id'];reports.append(report)
  if i==0:
   template=json.loads((run/'manifest.json').read_text());shutil.copytree(run/'assets',work/'assets')
  for file in ['.png','_preview.jpg']:shutil.copyfile(run/(l['id']+file),work/(l['id']+file))
  shutil.copyfile(run/'scene.usda',work/('scene_'+l['id']+'.usda'))
  image=cv2.imread(str(work/(l['id']+'_preview.jpg')));tile=cv2.resize(image,(320,240));cv2.putText(tile,l['id'],(8,20),cv2.FONT_HERSHEY_SIMPLEX,.5,(255,255,255),1);tiles.append(tile)
  mats='、'.join(m['name'] for m in l['required_materials'][1:]);cards.append(f'<article><h3>{l["id"]}</h3><img src="{l["id"]}_preview.jpg"><p>{html.escape(mats)}</p></article>')
  # Completed artifacts have been copied out; discard only this run's generated scratch.
  shutil.rmtree(run);print('COMPLETE',i+1,len(source['layouts']),l['id'],flush=True)
 out=copy.deepcopy(source)
 for key in ['render_kind','render_runtime','render_provenance','limitations']:out[key]=template[key]
 out['render_provenance']['isolated_process_per_layout']=True
 out['render_provenance']['batch_script_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
 (work/'manifest.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
 summary={'runtime':reports[0]['runtime'],'isolated_process_per_layout':True,'layouts':[r['layouts'][0] for r in reports],
  'projection_errors_px':[e for r in reports for e in r['projection_errors_px']],'hardware_started':False,'physics_stepped':False}
 (work/'render_report.json').write_text(json.dumps(summary,indent=2)+'\n')
 while len(tiles)%4:tiles.append(np.full((240,320,3),38,np.uint8))
 cv2.imwrite(str(work/'overview.jpg'),np.vstack([np.hstack(tiles[i:i+4]) for i in range(0,len(tiles),4)]))
 page='<!doctype html><meta charset="utf-8"><title>YAM 实体渲染指引</title><style>body{font:16px system-ui;background:#18212b;color:white;margin:24px}main{display:grid;grid-template-columns:repeat(3,1fr);gap:15px}img{width:100%}article{background:#263442;padding:12px}</style><h1>TASK-YAM-0015 · Isaac Sim 5.1 实体渲染指引</h1><p>上层抽屉初始关闭，每条3件物品。尺寸与布局坐标沿用候选包，外观为照片辅助参数化估计。每条独立渲染，避免上一布局残影；PNG背景透明，实时页面默认50%叠加。不是接触仿真或现场可达性验收。</p><main>'+''.join(cards)+'</main>'
 (work/'index.html').write_text(page);(work/'frames').rmdir();work.rename(a.output)
 print('PUBLISHED',a.output,flush=True)
if __name__=='__main__':main()
