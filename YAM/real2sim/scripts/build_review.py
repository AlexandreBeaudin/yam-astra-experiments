"""Package actual rendered frames into a self-contained local review page and video."""
import argparse,json,html
from pathlib import Path
import cv2,numpy as np,imageio.v2 as imageio
ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--run',type=Path,default=ROOT/'runs/stack_cups')
args=parser.parse_args();run=args.run
m=json.loads((run/'manifest.json').read_text());render=json.loads((run/'render_manifest.json').read_text())
frames=render['frames'];out=run/'review';out.mkdir(exist_ok=True)
for folder in ['frames','overlay']:(out/folder).mkdir(exist_ok=True)
fps=24/float(np.median(np.diff(frames))) if len(frames)>1 else 24.
video=imageio.get_writer(str(out/'comparison.mp4'),fps=fps,codec='libx264',quality=8,macro_block_size=1)
for frame in frames:
    real=[];sim=[];overlay=[]
    for cam in ['top','left','right']:
        a=cv2.imread(str(Path(m['cameras'][cam]['images'])/f'{frame}.jpg'))
        b=cv2.imread(str(run/'renders'/cam/f'{frame:04d}.png'))
        if a is None or b is None:raise ValueError(f'Missing frame {frame}/{cam}')
        overlay.append(cv2.addWeighted(a,.5,b,.5,0))
        a=a.copy();b=b.copy()
        for im,label in [(a,f'REAL | {cam} | frame {frame}'),(b,f'SIM | {cam} | visual replay')]:
            cv2.rectangle(im,(0,0),(640,32),(25,25,25),-1)
            cv2.putText(im,label,(12,23),cv2.FONT_HERSHEY_SIMPLEX,.6,(240,240,240),1,cv2.LINE_AA)
        real.append(a);sim.append(b)
    mosaic=np.concatenate([np.concatenate(real,1),np.concatenate(sim,1)],0)
    cv2.imwrite(str(out/'frames'/f'{frame:04d}.jpg'),mosaic,[cv2.IMWRITE_JPEG_QUALITY,92])
    cv2.imwrite(str(out/'overlay'/f'{frame:04d}.jpg'),np.concatenate(overlay,1),[cv2.IMWRITE_JPEG_QUALITY,92])
    video.append_data(cv2.cvtColor(mosaic,cv2.COLOR_BGR2RGB))
video.close()
checks=json.loads((run/'implementation_checks.json').read_text())
landmarks=json.loads((run/'landmark_metrics.json').read_text())
page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>Real2Sim · 叠杯回放验收</title>
<style>body{margin:0;background:#121820;color:#e6edf3;font:16px system-ui}main{max-width:1440px;margin:auto;padding:32px}h1{margin-bottom:6px}p{line-height:1.7;color:#b6c5d4}.tag{display:inline-block;padding:6px 12px;background:#193842;color:#a8ecdb;border-radius:20px}img{width:100%;background:#000;border-radius:10px}nav{display:flex;gap:15px;align-items:center;margin:22px 0}input{flex:1}button,select{padding:9px;background:#273647;color:white;border:1px solid #4b6074;border-radius:6px}a{color:#9be0d0}.note{padding:18px;background:#202c38;border-left:3px solid #e4b85b;margin:20px 0}details{margin:24px 0}pre{white-space:pre-wrap;font-size:12px}</style>
<main><span class="tag">真实数据 → 官方机器人资产 → Isaac Sim RTX</span>
<h1>叠杯任务 · 三视角回放</h1><p>上排是真机，下排是仿真；三个相机共享同一个三维场景。拖动时间轴查看，或切换到半透明叠加检查位置差异。</p>
<div class="note">当前为视觉回放原型：机器人来自记录状态，道具尺寸与抓取轨迹为估计，尚未验证自然接触物理。画面差异供本轮验收定位，不代表高精度数字孪生已完成。播放速度是展示设置，原始数据没有经过验证的采集时间戳。</div>
<nav><button id="play">播放</button><select id="mode"><option value="frames">真机 / 仿真</option><option value="overlay">半透明叠加</option></select><input id="slider" type="range" min="0" value="0"><span id="label"></span></nav>
<img id="view"><p><a href="comparison.mp4">下载/播放对比视频</a> · <a href="../scene_manifest.json">场景与估计项</a> · <a href="../implementation_checks.json">实现验证结果</a></p>
<details><summary>已完成与未完成的验证</summary><p>已检查关节方向/零位、独立姿态 FK、相机投影矩阵与动画帧完整性。另检查了同一片段中未用于抓取拟合的 5 个人工杯口中心：中位误差 LANDMARK_MEDIAN px，最大 LANDMARK_MAX px；人工参考点本身约有 10 px 不确定性，仅是稀疏检查。完整轮廓、遮挡、独立片段评估尚未通过，仍需修正道具与桌面外观，并验证夹爪接触与动力学。右基座外参仍为暂定结果。</p><p><a href="../landmark_metrics.json">杯口检查数据</a> · <a href="../landmark_checks.jpg">人工参考点与投影图</a></p><pre>CHECKS</pre></details>
</main><script>const frames=FRAMES;const slider=document.getElementById('slider'),view=document.getElementById('view'),mode=document.getElementById('mode');slider.max=frames.length-1;let timer=null;function show(){let n=frames[+slider.value];view.src=mode.value+'/'+String(n).padStart(4,'0')+'.jpg';document.getElementById('label').textContent='帧 '+n}slider.oninput=show;mode.onchange=show;document.getElementById('play').onclick=()=>{if(timer){clearInterval(timer);timer=null}else{timer=setInterval(()=>{slider.value=(+slider.value+1)%frames.length;show()},FRAME_INTERVAL)}document.getElementById('play').textContent=timer?'暂停':'播放'};show();</script></html>'''
page=page.replace('LANDMARK_MEDIAN',f"{landmarks['median_error_px']:.2f}").replace('LANDMARK_MAX',f"{landmarks['max_error_px']:.2f}")
page=page.replace('FRAME_INTERVAL',str(1000/fps)).replace('FRAMES',json.dumps(frames)).replace('CHECKS',html.escape(json.dumps(checks,ensure_ascii=False,indent=2)))
(out/'index.html').write_text(page)
print(out/'index.html')
