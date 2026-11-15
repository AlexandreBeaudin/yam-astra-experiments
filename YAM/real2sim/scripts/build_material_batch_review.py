"""Produce a review page and versioned local candidate index from actual batch outputs."""
import html,json,hashlib
from pathlib import Path
import cv2,numpy as np
ROOT=Path(__file__).resolve().parents[1];RUN=ROOT/'runs/material_reconstruction_batch'
batch=json.loads((RUN/'batch.json').read_text())
rois={'MAT-00004':[800,390,1250,850],'MAT-00010':[740,270,1100,1060],'MAT-00012':[840,440,1140,750],
      'MAT-00019':[245,680,890,1440],'MAT-00033':[615,375,1385,670],'MAT-00035':[230,640,720,1390],'MAT-00036':[140,460,805,1380]}
cards=[];index=[]
for a in batch['assets']:
    mid=a['material_id'];out=RUN/mid;out.mkdir(exist_ok=True)
    raw=cv2.imread(a['photo']);x0,y0,x1,y1=rois[mid];cv2.imwrite(str(out/'photo_crop.jpg'),raw[y0:y1,x0:x1])
    entry=dict(a)
    entry['asset_sha256']=hashlib.sha256(Path(a['asset_usd']).read_bytes()).hexdigest()
    entry['photo_crop_roi']=rois[mid]
    entry['quality']='simulation_candidate_pending_review'
    (Path(a['asset_usd']).parent/'manifest.json').write_text(json.dumps(entry,ensure_ascii=False,indent=2))
    index.append({'material_id':mid,'version':a['version'],'manifest':str(Path(a['asset_usd']).parent/'manifest.json'),
                  'geometry_candidate_usable':a.get('geometry_candidate_usable',False),'guidance_ready':False,'contact_validated':False})
    rows=''.join(f'<li>{html.escape(s)}</li>' for s in a['limitations'])
    phys=a.get('static_physics',{});badge='静置通过' if phys.get('passed') else '静置未通过'
    cards.append(f'''<article><h2>{mid} · {html.escape(a['name'])} <small>{badge}</small></h2>
    <p>登记尺寸：{html.escape(a['source_dimensions'])}　|　尺寸来源：{html.escape(a['dimensions_source'])}</p>
    <div class="images"><figure><img src="{mid}/photo_crop.jpg"><figcaption>实物照片裁切 · 非标定视角</figcaption></figure>
    <figure><img src="{mid}/oblique.png"><figcaption>RTX 三维斜视</figcaption></figure><figure><img src="{mid}/front.png"><figcaption>RTX 正视</figcaption></figure>
    <figure><img src="{mid}/top.png"><figcaption>RTX 顶视 · 内腔与轮廓</figcaption></figure></div>
    <p>网格检查：{'通过' if a['mesh_validation']['passed'] else '失败'}；碰撞体 {a['mesh_validation']['collider_count']} 个；尺寸资料{'完整（仅本候选所需外形）' if a['recipe']['dimension_complete'] else '缺失或冲突，需复核'}。</p>
    <details><summary>假设与待核对项</summary><ul>{rows}</ul></details></article>''')
page='''<!doctype html><html lang="zh"><meta charset="utf-8"><title>首批物料重建测试</title>
<style>body{background:#111b29;color:#edf1f8;font:15px system-ui;margin:0}main{max-width:1500px;margin:auto;padding:30px}h1{margin-top:0}p{color:#afbed2;line-height:1.6}article{background:#1b2a3e;padding:20px;margin:22px 0;border-radius:14px}h2{font-size:21px}small{font-size:14px;color:#6eddb5}.images{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}figure{margin:0}img{width:100%;height:260px;object-fit:contain;background:#d6d8da;border-radius:8px}figcaption{margin-top:6px;color:#c4cfdf}li{line-height:1.7}strong{color:#ffe19a}</style>
<main><h1>飞书物料 → 首批仿真重建测试</h1><p>7 件物料 · 21 次 PhysX 静置实验 · 21 张 RTX 渲染图。图片与三维视角未做相机配准，不作像素相似度比较。</p>
<p><strong>这是待复核候选，不是已认证数字孪生。</strong>模型由照片检查和显式尺寸配方生成；没有调用 SimFoundry 图生三维或深度后端。摩擦和质量为测试假设，没有执行机械臂抓取。</p>'''+''.join(cards)+'</main></html>'
(RUN/'index.html').write_text(page)
(RUN/'candidate_index.json').write_text(json.dumps(index,ensure_ascii=False,indent=2))
board=np.full((1160,1320,3),(35,26,18),np.uint8)
for i,a in enumerate(batch['assets']):
    x=(i%2)*660;y=(i//2)*290
    cv2.putText(board,a['material_id']+' | geometry + static checks '+('passed' if a.get('geometry_candidate_usable') else 'FAILED'),(x+16,y+28),cv2.FONT_HERSHEY_SIMPLEX,.52,(230,235,240),1,cv2.LINE_AA)
    for k,(file,label) in enumerate([('photo_crop.jpg','Real photo'),('oblique.png','RTX oblique'),('top.png','RTX top')]):
        source=cv2.imread(str(RUN/a['material_id']/file));h,w=source.shape[:2];scale=min(208/w,216/h)
        small=cv2.resize(source,(round(w*scale),round(h*scale)));px=x+10+k*218+(208-small.shape[1])//2;py=y+45+(216-small.shape[0])//2
        board[py:py+small.shape[0],px:px+small.shape[1]]=small
        cv2.putText(board,label,(x+16+k*218,y+281),cv2.FONT_HERSHEY_SIMPLEX,.45,(180,200,220),1,cv2.LINE_AA)
for y,label in [(940,'7 candidate assets / 21 settling trials'),(980,'No grasp / depth / multiview validation'),(1020,'Mass, friction and missing details assumed')]:
    cv2.putText(board,label,(685,y),cv2.FONT_HERSHEY_SIMPLEX,.56,(80,200,245),1,cv2.LINE_AA)
cv2.imwrite(str(RUN/'overview.png'),board)
metrics=dict(asset_count=len(index),mesh_pass=sum(a['mesh_validation']['passed'] for a in batch['assets']),
             static_trials=sum(len(a['static_physics']['trials']) for a in batch['assets']),
             static_trials_passed=sum(t['passed'] for a in batch['assets'] for t in a['static_physics']['trials']),
             render_count=len(list(RUN.glob('MAT-*/*.png'))),guidance_ready=0,contact_validated=0,simfoundry_backend_executed=False)
(RUN/'metrics.json').write_text(json.dumps(metrics,indent=2))
print(RUN/'index.html')
