"""Create review pages for actual physics traces; clearly separate recorded and synthetic tasks."""
import argparse,json,html
from pathlib import Path
import numpy as np,cv2,imageio.v2 as imageio
ROOT=Path(__file__).resolve().parents[1];p=argparse.ArgumentParser(description=__doc__);p.add_argument('--variant',default='nominal');args=p.parse_args()
run=ROOT/'runs/stack_cups_contact'/args.variant;out=run/'review';out.mkdir(exist_ok=True);(out/'frames').mkdir(exist_ok=True)
manifest=json.loads((run/'manifest.json').read_text());report=json.loads((run/'physics_report.json').read_text());render=json.loads((run/'render_manifest.json').read_text());frames=render['frames'];bench=report.get('scenario')=='grasp_bench';cams=['top','right'] if bench else ['top','left','right'];fps=24/float(np.median(np.diff(frames)))
writer=imageio.get_writer(str(out/'comparison.mp4'),fps=fps,codec='libx264',quality=8,macro_block_size=1)
for f in frames:
 real=[];sim=[]
 for cam in cams:
  b=cv2.imread(str(run/'renders'/cam/f'{f:04d}.png'));assert b is not None and b.shape==(480,640,3)
  cv2.rectangle(b,(0,0),(640,32),(22,30,40),-1);cv2.putText(b,f'PHYSICS | {cam} | frame {f}',(10,23),cv2.FONT_HERSHEY_SIMPLEX,.6,(245,245,245),1,cv2.LINE_AA);sim.append(b)
  if not bench:
   a=cv2.imread(str(Path(manifest['cameras'][cam]['images'])/f'{f}.jpg'));assert a is not None
   cv2.rectangle(a,(0,0),(640,32),(22,30,40),-1);cv2.putText(a,f'REAL | {cam} | frame {f}',(10,23),cv2.FONT_HERSHEY_SIMPLEX,.6,(245,245,245),1,cv2.LINE_AA);real.append(a)
 mosaic=np.concatenate(sim,1) if bench else np.concatenate([np.concatenate(real,1),np.concatenate(sim,1)],0)
 cv2.imwrite(str(out/'frames'/f'{f:04d}.jpg'),mosaic,[cv2.IMWRITE_JPEG_QUALITY,92]);writer.append_data(cv2.cvtColor(mosaic,cv2.COLOR_BGR2RGB))
writer.close()
eval_path=run.parent/'contact_evaluation.json';ev=json.loads(eval_path.read_text());summary=ev['runs'][args.variant]
title='合成测试：居中夹取 → 抬升 → 松开' if bench else '遥操轨迹接触回放：抓取、抬升与滑落'
note='这是合成夹爪运动测试，用于检查接触模型；不是遥操数据复现。杯子没有运动目标，运动完全来自物理求解。' if bench else '上排真机，下排接触仿真。机械臂跟随记录状态，夹爪由有限力驱动，杯子只由重力和接触运动。本轮叠杯失败，滑落过程保留供检查。'
link='<a href="../../nominal/review/index.html">查看遥操接触回放</a>' if bench else '<a href="../../grasp_bench_nominal/review/index.html">查看独立居中抓取测试</a>'
page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>TITLE</title><style>body{margin:0;background:#121820;color:#e6edf3;font:16px system-ui}main{max-width:1440px;margin:auto;padding:28px}p{line-height:1.7;color:#bbcbd6}.note{background:#253441;padding:18px;border-left:3px solid #dfb666}nav{display:flex;gap:18px;align-items:center;margin:20px 0}button{padding:10px;background:#315467;color:white;border:0;border-radius:6px}input{flex:1}img{width:100%;border-radius:9px}a{color:#9ce2d0}pre{white-space:pre-wrap;font-size:12px}details{margin-top:20px}</style><main><h1>TITLE</h1><p class="note">NOTE</p><p>尚未实测的参数：夹爪装配位姿、纸杯尺寸与质量、摩擦、电机力、采集时间间隔。没有“抓住后绑定”或释放后的杯子目标轨迹。</p><nav><button id="play">播放</button><input id="slider" type="range" min="0" value="0"><span id="label"></span></nav><img id="view"><p><a href="comparison.mp4">播放/下载视频</a> · LINK · <a href="../../contact_evaluation.json">接触与对照检查</a> · <a href="../physics_report.json">本次运行数据</a></p><details><summary>物理检查结果与局限</summary><pre>REPORT</pre></details></main><script>const frames=FRAMES,slider=document.getElementById('slider');slider.max=frames.length-1;let timer=null;function show(){let f=frames[+slider.value];document.getElementById('view').src='frames/'+String(f).padStart(4,'0')+'.jpg';document.getElementById('label').textContent='帧 '+f}slider.oninput=show;document.getElementById('play').onclick=()=>{if(timer){clearInterval(timer);timer=null}else timer=setInterval(()=>{slider.value=(+slider.value+1)%frames.length;show()},INTERVAL);document.getElementById('play').textContent=timer?'暂停':'播放'};show();</script></html>'''
for a,b in [('TITLE',title),('NOTE',note),('LINK',link),('REPORT',html.escape(json.dumps(summary,ensure_ascii=False,indent=2))),('FRAMES',json.dumps(frames)),('INTERVAL',str(1000/fps))]:page=page.replace(a,b)
(out/'index.html').write_text(page);print(out/'index.html')
