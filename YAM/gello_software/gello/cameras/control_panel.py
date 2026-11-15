"""Browser collection controls; no direct motor RPC is exposed."""

import json


def control_panel(lang):
    zh = lang == "zh"
    labels = (
        {
            "claim": "取得操作权限",
            "release": "释放操作权限",
            "start": "准备下一条（机械臂将移动）",
            "finish": "结束本条并标注",
            "success": "成功保存",
            "fail": "失败保存",
            "discard": "废弃本条",
            "stop": "停止任务（软件停止，非硬件急停）",
            "offline": "连接中断，控制状态未知",
            "confirm": "确定废弃本条数据？此操作不会保存。",
            "readonly": "只读，请取得操作权限",
            "owner": "本页面可操作 · 终端仍可操作",
            "pending": "正在处理，请等待",
            "error": "指令未确认，请查看当前状态；不会自动重发。",
            "initializing": "初始化中",
            "ready": "就绪",
            "syncing": "对齐中",
            "waiting_for_trigger": "未录制：先松开，再按到底，最后松开所有主臂扳机",
            "trigger_release_first": "未录制：请先松开所有主臂扳机",
            "trigger_press": "未录制：请同时按到底所有主臂扳机",
            "trigger_release": "未录制：请松开所有主臂扳机，等待对齐后录制",
            "recording": "录制中",
            "review": "等待标注",
            "fault": "故障",
            "stopped": "任务已停止",
            "note": "停止任务会废弃当前未保存数据并请求安全保持。关闭网页不会停止任务。电机操作仍在终端完成。",
        }
        if zh
        else {
            "claim": "Acquire control",
            "release": "Release control",
            "start": "Prepare next (robot will move)",
            "finish": "Finish and label",
            "success": "Save success",
            "fail": "Save failure",
            "discard": "Discard",
            "stop": "Stop task (software stop, not emergency stop)",
            "offline": "Disconnected; control state unknown",
            "confirm": "Discard this episode without saving?",
            "readonly": "Read only; acquire control",
            "owner": "This page has control; terminal remains available",
            "pending": "Processing; please wait",
            "error": "Command not confirmed; check current state. It will not be retried.",
            "initializing": "Initializing",
            "ready": "Ready",
            "syncing": "Syncing",
            "waiting_for_trigger": "Not recording: release, squeeze fully, then release all triggers",
            "trigger_release_first": "Not recording: release all triggers first",
            "trigger_press": "Not recording: fully squeeze all triggers",
            "trigger_release": "Not recording: release all triggers; waiting for alignment",
            "recording": "Recording",
            "review": "Awaiting label",
            "fault": "Fault",
            "stopped": "Stopped",
            "note": "Stop discards unsaved data and requests safe hold. Closing this page does not stop the task. Motor controls remain in the terminal.",
        }
    )
    return """<section id="collection"><strong id="collection-state"></strong>
<p id="collection-access"></p><div id="collection-buttons"></div><p id="collection-error" role="alert"></p>
<p id="collection-note"></p></section>
<style>body{padding-bottom:280px}#collection{position:fixed;bottom:12px;left:24px;right:24px;z-index:10;max-height:45vh;overflow:auto;background:#172330;padding:16px;margin-top:18px;border:1px solid #567;border-radius:12px}
#collection-buttons{display:flex;gap:10px;flex-wrap:wrap}button{padding:12px 18px;font:inherit;border:0;border-radius:8px;cursor:pointer;background:#4eafe9;color:#071520}
button:disabled{opacity:.35;cursor:default}button[data-action=stop],button[data-action=discard]{background:#e5877e}
#collection-error{color:#ffb5aa}#collection-note{font-size:13px}</style><script>
(()=>{
const text=LABELS, client=crypto.randomUUID(), buttons={};
const state=document.getElementById('collection-state'), access=document.getElementById('collection-access');
const error=document.getElementById('collection-error');
document.getElementById('collection-note').textContent=text.note;
let snapshot=null, busy=false, connected=false, lastStatus=0;
function disable(){for(const b of Object.values(buttons))b.disabled=true}
function render(){
  disable();
  if(!connected){state.textContent=text.offline;return}
  const s=snapshot;
  const phaseText=s.phase==='waiting_for_trigger' ? text['trigger_'+s.trigger_stage]||text[s.phase] : text[s.phase]||s.phase;
  state.textContent=`${s.task} · #${s.episode_index} · ${phaseText} · ${s.elapsed.toFixed(1)} s`;
  access.textContent=s.pending?text.pending:(s.owns_control?text.owner:text.readonly);
  for(const [name,b] of Object.entries(buttons)){
    const visible=['claim','release','stop'].includes(name)||
      (name==='start'&&s.phase==='ready')||(name==='finish'&&s.phase==='recording')||
      (['success','fail','discard'].includes(name)&&s.phase==='review');
    b.hidden=!visible;
    b.disabled=busy||!s.owns_control||s.pending||['fault','stopped','initializing'].includes(s.phase);
  }
  buttons.claim.disabled=busy||s.owns_control||!s.control_available;
  buttons.release.disabled=busy||!s.owns_control;
  buttons.stop.disabled=!s.owns_control||s.phase==='stopped';
}
async function request(action){
  const response=await fetch('control',{method:'POST',headers:{'Content-Type':'application/json',
    'X-YAM-Control':location.pathname.split('/')[1]},
    body:JSON.stringify({client,action,revision:snapshot?.revision}),signal:AbortSignal.timeout(2000)});
  const result=await response.json();if(!response.ok)throw Error(result.error||'Request failed');
  return result;
}
for(const name of ['claim','release','start','finish','success','fail','discard','stop']){
 const b=document.createElement('button');b.textContent=text[name];b.dataset.action=name;b.disabled=true;
 buttons[name]=b;document.getElementById('collection-buttons').append(b);
 b.onclick=async()=>{
   if(b.disabled)return;
   if(name==='discard'&&!confirm(text.confirm))return;
   busy=true;render();error.textContent='';
   try{snapshot=await request(name);lastStatus=performance.now();connected=true}catch(e){error.textContent=text.error;connected=false}
   finally{busy=false;render()}
 };
}
async function poll(){
 try{
  if(!busy&&!document.hidden){snapshot=await request('status');lastStatus=performance.now();connected=true;
    if(snapshot.message)error.textContent=snapshot.message;render()}
 }catch(e){connected=false;render()}
 setTimeout(poll,500);
}
setInterval(()=>{if(document.hidden||performance.now()-lastStatus>2500){connected=false;render()}},250);
render();poll();
})();
</script>""".replace("LABELS", json.dumps(labels))
