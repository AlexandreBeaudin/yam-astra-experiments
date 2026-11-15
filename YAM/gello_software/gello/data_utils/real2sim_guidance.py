"""Optional, hardware-free adapter from shared guidance bundles to YAM MCAP."""
import os
import sys
from pathlib import Path

def guidance_session_type():
    """Load the pinned submodule, or an explicitly selected workbench checkout."""
    override = os.environ.get("REAL2SIM_ROOT")
    root = Path(override) if override else Path(__file__).resolve().parents[3] / "real2sim"
    source = root / "src"
    if override and not (source / "real2sim" / "guidance.py").is_file():
        raise RuntimeError(f"REAL2SIM_ROOT has no guidance module: {root}")
    if (source / "real2sim" / "guidance.py").is_file():
        if str(source) not in sys.path:
            sys.path.insert(0, str(source))
    try:
        from real2sim.guidance import GuidanceSession
    except ModuleNotFoundError as exc:
        if exc.name != "real2sim":
            raise
        raise RuntimeError(
            "Initialize the real2sim submodule with "
            "git submodule update --init real2sim, then install the guidance extra"
        ) from exc
    return GuidanceSession


def session(bundle, camera_config=None):
    GuidanceSession = guidance_session_type()
    main=camera_config.cameras.get('main') if camera_config is not None else None
    result=GuidanceSession(bundle,platform='yam',camera_size=(main.width,main.height) if main else None)
    if main is not None:
        camera=result.manifest['camera']
        if camera.get('serial')!=main.serial or camera.get('saved_image_rotation_deg')!=main.rotation:
            raise ValueError('Guidance calibration serial/rotation does not match main camera')
    return result


def panel():
    return r'''<section style="background:#1b2938;padding:12px;margin:12px 0"><b>初始摆放指引</b> <button id="guide-prev">上一条</button><button id="guide-next">下一条</button><label> 叠加透明度 <input id="guide-opacity" type="range" min="0" max="1" step="0.05" value="0.5"></label><span id="guide-label" style="position:static"></span><p>按投影摆放，再按原扳机流程开始；录完手动还原后切换指引。无需标点。目标写入 MCAP，实际摆放待首帧核对。</p></section>
<script>
(()=>{let state, busy=false, image=new Image();const main=document.querySelector('img[data-camera="main"]');if(!main)return;const canvas=document.createElement('canvas');canvas.style.cssText='position:absolute;pointer-events:none;z-index:2';main.parentElement.append(canvas);const ctx=canvas.getContext('2d'),label=document.getElementById('guide-label');
function draw(){if(!state||!image.complete||!image.naturalWidth)return;let w=main.clientWidth,h=main.clientHeight,scale=Math.min(w/image.naturalWidth,h/image.naturalHeight);canvas.width=image.naturalWidth;canvas.height=image.naturalHeight;canvas.style.width=image.naturalWidth*scale+'px';canvas.style.height=image.naturalHeight*scale+'px';canvas.style.left=(w-image.naturalWidth*scale)/2+'px';canvas.style.top=main.offsetTop+(h-image.naturalHeight*scale)/2+'px';ctx.clearRect(0,0,canvas.width,canvas.height);ctx.globalAlpha=state.opacity;ctx.drawImage(image,0,0)}
async function update(delta){if(busy)return;busy=true;try{let response;if(delta!==undefined){response=await fetch('guidance',{method:'POST',headers:{'Content-Type':'application/json','X-YAM-Control':location.pathname.split('/')[1]},body:JSON.stringify({index:Math.max(0,Math.min(state.count-1,state.index+delta)),opacity:Number(document.getElementById('guide-opacity').value)})})}else response=await fetch('guidance');if(!response.ok)throw Error('指引服务不可用');const next=await response.json();if(!state||state.index!==next.index)image.src='guidance/projection/'+next.index;state=next;document.getElementById('guide-opacity').value=state.opacity;label.textContent=` ${state.index+1}/${state.count} · ${state.layout_id} · ${state.task_id} · ${state.deployment_status==='candidate_pending_field_acceptance'?'候选指引：现场边界/TCP待核实 · ':''}${(state.required_materials||[]).map(m=>(m.name||m.material_id)+' '+m.material_id+' ×'+m.quantity).join(' / ')} · ${state.task_rules?.target_drawer==='upper'?'上层抽屉，初始关闭；全部放入同一层 · ':''}待首帧核对`;document.getElementById('guide-prev').disabled=state.index===0;document.getElementById('guide-next').disabled=state.index===state.count-1;draw()}catch(e){label.textContent=e.message}finally{busy=false}}
document.getElementById('guide-opacity').onchange=()=>update(0);image.onload=draw;new ResizeObserver(draw).observe(main);document.getElementById('guide-prev').onclick=()=>update(-1);document.getElementById('guide-next').onclick=()=>update(1);update();setInterval(()=>update(),1000)})();
</script>'''
