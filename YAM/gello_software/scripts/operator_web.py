"""Local operator workbench. The safety daemon remains the sole motor authority."""
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from gello.safety.rpc import SafetyClient
from gello.profile import load_profile
from gello.data_utils.mcap_recording import McapCatalog

class Workbench:
    def __init__(self):
        self.lock=threading.RLock();self.process=None;self.busy=False;self.revision=0
        self.message='选择任务并执行设备检查';self.events=[];self.results=[];self.task='';self.output=[]
        self.check_passed=False;self.notice_level='info';self.current_action=None;self.phase='idle';self.mode='collection'
        self.device_owner=None
        self.profile=load_profile(None);self.safety_port=self.profile.network.safety_port
        self.directory=Path.home()/'.local/state/yam-operator';self.directory.mkdir(parents=True,exist_ok=True)
    def safety(self):
        c=SafetyClient(port=self.safety_port,timeout_ms=500)
        try:return c.status()
        except Exception:return dict(state='offline',hold_confirmed=False)
        finally:c.close()
    def collection(self, action='status', client='yam-operator-workbench', release=True, expected_revision=None):
        base='http://127.0.0.1:8765'
        req=urllib.request.Request(base+'/preview-session',headers={'X-YAM-Preview':'1'})
        path=json.load(urllib.request.urlopen(req,timeout=2))['path']
        def send(a,revision=None):
            r=urllib.request.Request(base+path+'control',data=json.dumps(dict(client=client,action=a,revision=revision)).encode(),headers={'Content-Type':'application/json','X-YAM-Control':path.strip('/')})
            return json.load(urllib.request.urlopen(r,timeout=2))
        if action in ('status','claim'):return send(action)
        # Stop is still subject to the collector's ownership rules.
        state=send('claim')
        try:
            if expected_revision is not None and expected_revision!=state['revision']:raise ValueError('State changed; refresh status')
            return send(action,state['revision'])
        finally:
            if release:send('release')
    def collector_running(self):
        try:return self.collection().get('phase') != 'stopped'
        except Exception:return False
    def snapshot(self):
        with self.lock:
            return dict(revision=self.revision,busy=self.busy,running=self.process is not None and self.process.poll() is None,
                message=self.message,notice_level=self.notice_level,check_passed=self.check_passed,current_action=self.current_action,phase=self.phase,mode=self.mode,task=self.task,events=self.results[-30:],output=self.output[-16:],safety=self.safety())
    def start(self,task,guided,platform_context=None):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}',task):raise ValueError('任务名仅允许字母、数字、下划线和短横线')
        if (self.process and self.process.poll() is None) or self.collector_running():raise ValueError('已有任务运行')
        s=self.safety()
        if s['state'] in ('active','initializing','fault','disarmed'):raise ValueError('当前安全状态不允许启动：'+s['state'])
        command=[str(ROOT/'scripts/teleop'),'--task-name',task,'--recording-format','mcap','--control-hz','200','--state-hz','200','--yes']
        if guided:
            from guided_collection import prepare
            bundle,_=prepare(task,Path.home()/'.config/yam/guidance.json')
            command+=['--guidance-bundle',str(bundle)]
        env=dict(os.environ,YAM_WEB_MANAGED='1',PYTHONUNBUFFERED='1')
        env.pop('YAM_PLATFORM_CONTEXT',None)
        if platform_context:env['YAM_PLATFORM_CONTEXT']=platform_context
        self.mode='collection';self.task=task;self.events=[];self.results=[];self.output=[];self.phase='initializing';self.notice_level='progress'
        self.process=subprocess.Popen(["sg","dialout","-c",shlex.join(command)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,env=env)
        threading.Thread(target=self.read,args=(self.process,),daemon=True).start()
        self.message='正在检查设备并使能。完成后点击“开始采集”，进行对齐与扳机确认。'
    def start_guidance_preview(self):
        if (self.process and self.process.poll() is None) or self.collector_running():raise ValueError('请先停止当前任务，再打开摆放测试')
        bundle=Path.home()/'.local/share/yam/simulated-placement/abc-d405-v1/bundle'
        if not (bundle/'manifest.json').is_file():raise ValueError('尚未生成 ABC 模拟参数')
        command=[sys.executable,str(ROOT/'scripts/preview_abc_guidance.py'),'--bundle',str(bundle)]
        self.mode='guidance_preview';self.phase='initializing';self.task='ABC-D405-PLACEMENT-TEST';self.events=[];self.results=[];self.output=[]
        self.process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
        threading.Thread(target=self.read,args=(self.process,),daemon=True).start()
        self.message='正在打开三路相机；本次只测试模拟标定投影，不使能机械臂。';self.notice_level='progress'
    def read(self,proc):
        log=self.directory/f'operator-{time.time_ns()}.log'
        with log.open('w') as f:
            for line in proc.stdout:
                f.write(line);f.flush()
                with self.lock:
                    self.output.append(line.strip());self.output=self.output[-100:]
                    if 'YAM_WEB_EVENT ' in line:
                        try:
                            e=json.loads(line.split('YAM_WEB_EVENT ',1)[1]);self.events.append(e)
                            self.events=self.events[-100:]
                            if e.get('type') in ('episode_committed','episode_discarded','save_failed','sync_failed','safety_fault','leader_fault'):
                                self.results.append(e)
                                with (self.directory/'results.jsonl').open('a') as audit:audit.write(json.dumps(dict(task=self.task,event=e),ensure_ascii=False)+'\n')
                            phases={'guidance_preview_ready':'guidance_preview','ready':'ready','next_ready':'ready','sync_started':'syncing','waiting_for_trigger':'waiting_for_trigger','episode_started':'recording','awaiting_label':'review','safe_hold_active':'stopped','safety_fault':'fault','leader_fault':'fault','camera_fault':'fault'}
                            if e.get('type') in phases:self.phase=phases[e['type']]
                            if e.get('type')=='guidance_preview_ready':
                                self.message='ABC 模拟标定摆放测试已就绪。请切换布局、调整透明度，核对主视角叠加。当前不录制、不控制机械臂。';self.notice_level='success'
                            if e.get('type') in ('ready','next_ready'):
                                self.message='设备已就绪。摆放完成后，点击“开始采集”。';self.notice_level='success'
                            elif e.get('type') in ('sync_failed','safety_fault','leader_fault','camera_fault','save_failed'):
                                self.message=e.get('message','操作未完成');self.notice_level='error'
                        except ValueError:pass
        code=proc.wait()
        with self.lock:
            self.message=('任务已停止。请确认双臂已支撑，再点击“下使能”。' if code==0 else f'任务异常退出（{code}），请查看详细日志：{log}')
            self.phase='stopped'
            if self.mode=='guidance_preview':
                self.phase='idle';self.message=('摆放测试已结束，相机已释放；未使能机械臂。' if code==0 else f'摆放测试启动失败，详见日志：{log}')
            self.notice_level='success' if code==0 else 'error';self.revision+=1
    def shutdown(self):
        # Stop our collector, but never terminate the persistent safety daemon.
        proc=self.process
        if proc is not None and proc.poll() is None:
            try:
                proc.stdin.write('stop\n');proc.stdin.flush();proc.wait(timeout=15)
            except Exception:
                c=SafetyClient(port=self.safety_port)
                try:
                    if c.status().get('state')=='initializing':c.cancel_initialization('operator service stopping')
                    else:c.safe_hold('operator service stopping')
                finally:c.close()
    def action(self,data):
        if not isinstance(data,dict):raise ValueError('请求必须为对象')
        with self.lock:
            if self.busy or data.get('revision')!=self.revision:raise ValueError('状态已变化，请刷新后重试')
            action=data.get('action')
            if action in ('check','enable'):
                if (Path.home()/'.config/yam/setup/maintenance').exists():raise ValueError('工位配置正在应用，请稍后再试')
                from gello.station_setup import validate_bindings
                validate_bindings()
            if self.device_owner and data.get('_device_session')!=self.device_owner and action not in ('stop','off'):raise ValueError('RDP 页面正在控制设备，请先结束并断开该会话')
            if data.get('_platform_context') and (not self.device_owner or data.get('_device_session')!=self.device_owner):raise ValueError('平台上下文需要有效设备会话')
            if action not in ('check','enable','stop','off','recover','guidance_preview'):raise ValueError('未知操作')
            if action in ('enable','off') and data.get('confirmed') is not True:raise ValueError('请先完成现场确认')
            self.busy=True;self.revision+=1;self.current_action=action;self.notice_level='progress'
            self.message={'check':'正在检查主臂、CAN 和三路相机，请稍候…','enable':'正在启动设备…','stop':'正在停止任务并确认安全保持…','off':'正在下使能，请保持双臂有支撑…','recover':'正在恢复未完成记录…','guidance_preview':'正在启动模拟标定摆放预览…'}[action]
        def run():
            try:
                if action=='check':
                    if (self.process and self.process.poll() is None) or self.collector_running():raise ValueError('运行中无需重复打开设备检查')
                    s=self.safety()
                    if s['state']=='active':raise ValueError('存在活动控制会话，请先停止')
                    r=subprocess.run(['sg','dialout','-c',shlex.join([str(ROOT/'scripts/teleop'),'--check'])],capture_output=True,text=True,timeout=50)
                    self.message=r.stdout+r.stderr
                    self.check_passed=r.returncode==0
                    if self.check_passed:self.phase='idle'
                    self.notice_level='success' if self.check_passed else 'error'
                    if r.returncode:self.message='检查未通过：\n'+self.message
                elif action=='guidance_preview':self.start_guidance_preview()
                elif action=='enable':self.start(data.get('task',''),data.get('guided') is True,data.get('_platform_context'))
                elif action=='stop':
                    try:
                        if self.process and self.process.poll() is None:
                            self.process.stdin.write('stop\n');self.process.stdin.flush()
                        else:self.collection('stop')
                    except Exception as exc:
                        # Always attempt HOLD, even if browser ownership prevents graceful stop.
                        c=SafetyClient(port=self.safety_port)
                        try:c.safe_hold('operator web requested stop')
                        finally:c.close()
                        raise RuntimeError('已请求安全保持；任务停止未确认：'+str(exc))
                    self.message='已请求停止任务；未保存记录将废弃，等待确认安全保持'
                elif action=='off':
                    if (self.process and self.process.poll() is None) or self.collector_running():raise ValueError('请先停止任务，等待进程结束后下使能')
                    s=self.safety()
                    if s['state'] not in ('safe_hold','gravity','fault'):raise ValueError('当前状态不允许下使能')
                    c=SafetyClient(port=self.safety_port)
                    try:
                        result=c.disarm(s.get('session_id') or 'operator-fault-off','off')
                        if result.get('state')!='disarmed':raise RuntimeError('下使能未确认')
                    finally:c.close()
                    self.message='电机下使能已确认。本次工作已结束。';self.notice_level='success';self.phase='off';self.check_passed=False
                elif action=='recover':
                    if self.process and self.process.poll() is None:raise ValueError('请先停止任务')
                    task=data.get('task','')
                    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}',task):raise ValueError('无效任务名')
                    catalog=McapCatalog(self.profile.storage.root,task)
                    paths=[str(catalog.recover_incomplete(p,'fail')) for p in list(catalog.incomplete_recordings)]
                    self.message='恢复为失败记录：'+json.dumps(paths,ensure_ascii=False)
            except Exception as exc:self.message='操作未完成：'+str(exc);self.notice_level='error'
            finally:
                with self.lock:
                    self.busy=False;self.revision+=1
                    with (self.directory/'actions.jsonl').open('a') as audit:
                        audit.write(json.dumps(dict(time_ns=time.time_ns(),action=action,message=self.message),ensure_ascii=False)+'\n')
        threading.Thread(target=run,daemon=True).start()

HTML=(ROOT/'scripts/operator_ui.html').read_text()

def main():
    app=Workbench();token=secrets.token_urlsafe(24)
    device_server=None;local_device_server=None
    if os.environ.get('YAM_DEVICE_CONFIG'):
        from device_api import create_server
        device_server=create_server(app,json.loads(Path(os.environ['YAM_DEVICE_CONFIG']).read_text()))
        local_device_server=create_server(app,json.loads(Path(os.environ['YAM_DEVICE_CONFIG']).read_text()),bridge=device_server.bridge,loopback=True)
        threading.Thread(target=device_server.serve_forever,daemon=True).start()
        threading.Thread(target=local_device_server.serve_forever,daemon=True).start()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def reply(self,code,data,kind='application/json'):
            body=data.encode() if isinstance(data,str) else json.dumps(data,ensure_ascii=False).encode()
            self.send_response(code);self.send_header('Content-Type',kind+'; charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('X-Frame-Options','SAMEORIGIN');self.end_headers();self.wfile.write(body)
        def local(self):
            host=self.headers.get('Host','')
            return bool(re.fullmatch(r'(localhost|127\.0\.0\.1):8770',host)) and self.headers.get('Sec-Fetch-Site')!='cross-site' and self.headers.get('Origin','http://'+host)=='http://'+host
        def proxy(self):
            path='/' if self.path=='/collector' else self.path
            if path=='/':
                from gello.cameras.preview import CameraPreview
                return self.reply(200,CameraPreview.entry_page(None).decode(),'text/html')
            headers={'Host':'127.0.0.1:8765'}
            for key in ('X-YAM-Preview','X-YAM-Control','Content-Type'):
                if self.headers.get(key):headers[key]=self.headers[key]
            body=None
            if self.command=='POST':
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<4096:return self.reply(400,{})
                body=self.rfile.read(size)
            try:
                req=urllib.request.Request('http://127.0.0.1:8765'+path,data=body,headers=headers)
                with urllib.request.urlopen(req,timeout=3) as response:
                    if 'text/html' in response.headers.get('Content-Type',''):
                        html=response.read().decode().replace('电机操作仍在终端完成。','上使能与下使能请使用工作台按钮。').replace('下方操作栏与终端同步','下方操作栏控制当前任务').replace('本页面可操作 · 终端仍可操作','本页面可操作').replace('YAM 相机实时预览','摆放与采集')
                        from operator_ui import enhance_collector
                        return self.reply(response.status,enhance_collector(html),'text/html')
                    self.send_response(response.status)
                    self.send_header('Content-Type',response.headers.get('Content-Type','application/octet-stream'))
                    self.send_header('Cache-Control','no-store');self.end_headers()
                    while True:
                        chunk=response.read1(65536)
                        if not chunk:break
                        self.wfile.write(chunk);self.wfile.flush()
            except urllib.error.HTTPError as exc:self.reply(exc.code,exc.read().decode())
            except (BrokenPipeError,ConnectionResetError):pass
            except Exception:self.reply(503,{'error':'采集尚未启动或已停止'})
        def do_GET(self):
            if not self.local():return self.reply(403,{'error':'仅允许本机访问'})
            if self.path=='/':return self.reply(200,HTML.replace('TOKEN',json.dumps(token)),'text/html')
            if self.path=='/api/status':return self.reply(200,app.snapshot())
            self.proxy()
        def do_POST(self):
            if not self.local():return self.reply(403,{'error':'无效来源'})
            if self.path.endswith('/control'):return self.proxy()
            if self.path.endswith('/guidance'):return self.proxy()
            if self.headers.get('X-YAM-Operator')!=token:return self.reply(403,{'error':'无效会话'})
            if self.path!='/api/action':return self.reply(404,{})
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<4096:raise ValueError('无效请求大小')
                app.action(json.loads(self.rfile.read(length)));self.reply(202,{'accepted':True})
            except (ValueError,TypeError,KeyError) as exc:self.reply(409,{'error':str(exc)})
    def stop_service(*_):
        try:
            if local_device_server:
                local_device_server.shutdown();local_device_server.server_close()
            if device_server:
                device_server.shutdown();device_server.server_close()
            app.shutdown()
        finally:raise SystemExit(0)
    signal.signal(signal.SIGTERM,stop_service)
    signal.signal(signal.SIGINT,stop_service)
    print('http://127.0.0.1:8770/',flush=True)
    server=ThreadingHTTPServer(('127.0.0.1',8770),Handler)
    # sg grants the already-authorized dialout group; tell systemd to supervise
    # this Python child so KillMode=process never leaves the workbench orphaned.
    notify=os.environ.get('NOTIFY_SOCKET')
    if notify:
        with socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM) as sock:
            sock.connect('\0'+notify[1:] if notify.startswith('@') else notify)
            sock.sendall(f'READY=1\nMAINPID={os.getpid()}'.encode())
    server.serve_forever()

if __name__=='__main__':main()
