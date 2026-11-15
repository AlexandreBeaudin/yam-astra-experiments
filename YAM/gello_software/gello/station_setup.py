"""Local-only station provisioning; discovers identities without motor commands."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import socket
import ssl
import subprocess
import threading
import tempfile
import urllib.request
from urllib.parse import urlsplit
import yaml
from gello.profile import load_profile, default_profile_path

ROOT=Path.home()/'.config/yam'
LOCK=threading.RLock()

def private_write(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.tmp-'+secrets.token_hex(4))
    fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as f:f.write(data);f.flush();os.fsync(f.fileno())
    os.replace(temp,path)

def discover():
    serials=[]
    for p in sorted(Path('/dev/serial/by-id').glob('*')):
        serials.append(dict(path=str(p),readable=os.access(p,os.R_OK|os.W_OK)))
    cans=[]
    for p in sorted(Path('/sys/class/net').glob('*')):
        try:
            if (p/'type').read_text().strip()!='280':continue
            serial=''
            for parent in (p/'device').resolve().parents:
                if (parent/'serial').exists():serial=(parent/'serial').read_text().strip();break
            cans.append(dict(channel=p.name,serial=serial))
        except OSError:continue
    cameras=[];warning=''
    try:
        import pyrealsense2 as rs
        for d in rs.context().query_devices():
            cameras.append(dict(serial=d.get_info(rs.camera_info.serial_number),model=d.get_info(rs.camera_info.name)))
    except Exception:warning='相机扫描失败，请检查RealSense驱动和USB权限'
    return dict(leaders=serials,cans=cans,cameras=cameras,warning=warning)

def current():
    try:
        p=load_profile(None)
        arms={side:dict(leader=str(a.leader_port),can=a.can_channel) for side,a in p.arms.items()}
        camera=yaml.safe_load(p.cameras.config_path.read_text())['cameras'] if p.cameras else {}
        cameras={k:v['serial'] for k,v in camera.items()}
        return dict(station_id=p.name,arms=arms,cameras=cameras,storage=str(p.storage.root))
    except (ValueError,OSError):return dict(station_id='',arms={},cameras={},storage=str(Path.home()/'data/YAM'))

def device_config_path():
    marker=ROOT/'setup/active.json'
    if marker.exists():
        path=json.loads(marker.read_text()).get('device_config')
        if path:return Path(path)
    return Path(os.environ.get('YAM_DEVICE_CONFIG',str(ROOT/'rdp-device.json')))

def fingerprint():
    parts=[]
    try:
        p=load_profile(None)
        for f in [p.path,*[a.config_path for a in p.arms.values()],p.cameras.config_path if p.cameras else p.path]:parts.append(f.read_bytes())
    except (ValueError,OSError):parts.append(b'no-profile')
    for f in (ROOT/'setup/active.json',device_config_path()):
        if f.exists():parts.append(f.read_bytes())
    return hashlib.sha256(b'\0'.join(parts)).hexdigest()

def safe_to_configure():
    op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with op.open('http://127.0.0.1:8770/api/status',timeout=3) as r:s=json.load(r)
        if s['running'] or s['busy'] or s['safety']['state'] not in ('offline','disarmed'):
            raise ValueError('请先停止采集并可靠支撑双臂、下使能，再配置工位')
    except urllib.error.URLError as exc:
        if not isinstance(exc.reason, ConnectionRefusedError):raise ValueError('无法确认工作台状态，请恢复服务后重试')
        # A failed workbench must not be confused with a stopped safety daemon.
        try:port=load_profile(None).network.safety_port
        except (ValueError,OSError):port=6002
        try:
            with socket.create_connection(('127.0.0.1',port),timeout=.5):pass
        except OSError:pass
        else:raise ValueError('安全服务仍运行但工作台不可用，请恢复工作台并确认下使能')
    try:port=load_profile(None).network.safety_port
    except (ValueError,OSError):port=6002
    if 's' in locals() and s['safety']['state']=='offline':
        try:
            with socket.create_connection(('127.0.0.1',port),timeout=.5):pass
        except OSError:pass
        else:raise ValueError('安全服务端口仍在线但状态未知，请恢复状态确认后重试')
    from gello.data_utils.platform_progress import Outbox
    q=Path.home()/'.local/state/yam-operator/rdp-progress.sqlite3'
    if q.exists():
        with Outbox(q).db() as db:
            unfinished=db.execute("SELECT count(*) FROM events a WHERE json_extract(a.payload,'$.event.kind')='started' AND NOT EXISTS (SELECT 1 FROM events b WHERE b.batch=a.batch AND json_extract(b.payload,'$.event.episode_id')=json_extract(a.payload,'$.event.episode_id') AND json_extract(b.payload,'$.event.kind') IN ('saved','discarded','stopped') AND b.state<>'publishing')").fetchone()[0]
            if unfinished or db.execute("SELECT count(*) FROM events WHERE state<>'synced'").fetchone()[0]:
                raise ValueError('仍有待同步进度，请完成同步后再配置工位')

def validate_bindings():
    manifest=ROOT/'setup/active.json'
    if not manifest.exists():return
    expected=json.loads(manifest.read_text());actual=discover()
    for side,a in expected['arms'].items():
        if not any(c['channel']==a['can'] and c['serial']==a['can_serial'] for c in actual['cans']):
            raise ValueError(f'{side} CAN身份或名称已变化，请打开本机配置向导重新确认')
        if not any(p['path']==a['leader'] and p['readable'] for p in actual['leaders']):
            raise ValueError(f'{side} 主臂未连接或无权限，请检查连接')

def plan(body):
    if body.get('revision')!=fingerprint():raise ValueError('配置已变化，请重新扫描后配置')
    inv=discover();old=current();arms=body.get('arms',{});cameras=body.get('cameras',{})
    if set(arms)!={'left','right'} or set(cameras)!={'main','left','right'}:raise ValueError('请选择左右双臂与三个相机视角')
    if len({a['leader'] for a in arms.values()})!=2 or len({a['can'] for a in arms.values()})!=2 or len(set(cameras.values()))!=3:raise ValueError('同一设备不能重复绑定多个角色')
    for side,a in arms.items():
        if not any(p['path']==a['leader'] and p['readable'] for p in inv['leaders']):raise ValueError(f'{side}主臂不存在或没有串口权限')
        matches=[c for c in inv['cans'] if c['channel']==a['can'] and c['serial']]
        if len(matches)!=1:raise ValueError('CAN适配器必须可读取唯一序列号')
        a['can_serial']=matches[0]['serial']
    if any(not any(c['serial']==sn and 'D405' in c['model'] for c in inv['cameras']) for sn in cameras.values()):raise ValueError('当前向导要求三台已连接的D405')
    if len({a['can_serial'] for a in arms.values()})!=2:raise ValueError('CAN序列号重复，无法可靠识别')
    bundle=body.get('bundle');configpath=device_config_path()
    config=json.loads(configpath.read_text()) if configpath.exists() else {}
    token=None
    if bundle:
        if bundle.get('schema_version')!=1:raise ValueError('接入文件版本不支持')
        for key in ('device_id','station_id'):
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}',bundle.get(key,'')):raise ValueError('接入文件中的设备身份无效')
        if len(base64.b64decode(bundle.get('public_key',''),validate=True))!=32:raise ValueError('平台公钥无效')
        if not re.fullmatch(r'[A-Za-z0-9_-]{43}',bundle.get('report_token','')):raise ValueError('设备凭据无效')
        for url in [bundle['url'],*bundle['portal_origins']]:
            u=urlsplit(url)
            if u.scheme!='https' or not u.hostname or u.username or u.path or u.query or u.fragment:raise ValueError('平台与设备地址必须为HTTPS来源地址')
        if not bundle['portal_origins']:raise ValueError('缺少平台地址')
        config.update({k:bundle[k] for k in ('device_id','station_id','url','portal_origins','public_key')})
        token=bundle['report_token'];config['report_url']=bundle['portal_origins'][0]+'/api/device/collection-events'
    if not config.get('station_id'):raise ValueError('请先导入平台下载的接入配置')
    for key in ('tls_cert','tls_key'):
        value=body.get(key) or config.get(key,'')
        if not Path(value).is_file():raise ValueError('请填写本机有效HTTPS证书及私钥路径')
        config[key]=str(Path(value).resolve())
    ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);ctx.load_cert_chain(config['tls_cert'],config['tls_key'])
    certificate=ssl._ssl._test_decode_cert(config['tls_cert'])
    ssl.match_hostname(certificate,urlsplit(config['url']).hostname)
    config['bind']='0.0.0.0'
    armconfigs={}
    for side,a in arms.items():
        uploaded=body.get('calibrations',{}).get(side)
        same=old['arms'].get(side,{}).get('leader')==a['leader']
        # An existing wizard manifest also protects against a swapped CAN adapter.
        previous=ROOT/'setup/active.json'
        if previous.exists():same=same and json.loads(previous.read_text())['arms'].get(side,{}).get('can_serial')==a['can_serial']
        else:
            inventory=ROOT/'inventory.json'
            known=json.loads(inventory.read_text()).get('followers',{}) if inventory.exists() else {}
            same=same and known.get(a['can_serial'],{}).get('channel')==old['arms'].get(side,{}).get('can')
        if uploaded:cfg=yaml.safe_load(uploaded)
        elif same:cfg=yaml.safe_load(load_profile(None).arms[side].config_path.read_text())
        else:raise ValueError(f'{side}为新硬件映射，请上传这套主从臂的已标定YAML配置')
        if cfg['robot'].get('_target_')!='gello.robots.yam.YAMRobot' or cfg['agent'].get('_target_')!='gello.agents.gello_agent.GelloAgent' or cfg['agent']['dynamixel_config'].get('_target_')!='gello.agents.gello_agent.DynamixelRobotConfig':raise ValueError('标定文件不是支持的YAM双臂配置')
        cfg['robot']['channel']=a['can'];cfg['agent']['port']=a['leader']
        from gello.profile import _load_arm
        with tempfile.TemporaryDirectory() as temp:
            candidate=Path(temp)/'arm.yaml';candidate.write_text(yaml.safe_dump(cfg));_load_arm(side,candidate)
        armconfigs[side]=cfg
    storage=Path(body.get('storage',old['storage'])).expanduser()
    if not storage.is_absolute():raise ValueError('数据目录须为绝对路径')
    return dict(arms=arms,cameras=cameras,armconfigs=armconfigs,config=config,configpath=configpath,token=token,storage=str(storage))

def _apply(body):
    with LOCK:
        if body.get('confirmed') is not True:raise ValueError('请确认左右设备、标定归属及已下使能')
        safe_to_configure();p=plan(body)
        directory=ROOT/'setup'/('version-'+secrets.token_hex(8));directory.mkdir(parents=True,mode=0o700)
        for side,cfg in p['armconfigs'].items():private_write(directory/(side+'.yaml'),yaml.safe_dump(cfg))
        cam=dict(cameras={role:dict(serial=sn,model='D405',width=640,height=480,fps=30,rotation=0,auto_exposure=True) for role,sn in p['cameras'].items()},depth={'default_enabled':False},sync={'max_skew_ms':25,'max_frame_age_ms':100,'max_consecutive_failures':10})
        private_write(directory/'cameras.yaml',yaml.safe_dump(cam))
        profile=dict(schema_version=1,name=p['config']['station_id'],arms={side:str(directory/(side+'.yaml')) for side in ('left','right')},cameras=dict(config=str(directory/'cameras.yaml'),required=True),storage=dict(root=p['storage'],min_free_gb=50),network=dict(host='127.0.0.1',hardware_port=6001,safety_port=6002),safety=dict(profile='yam-v1',watchdog_ms=400,motors_per_arm=7))
        # Preserve existing network and storage options when already installed.
        try:
            old=load_profile(None);original=yaml.safe_load(old.path.read_text());profile['network']=original['network'];profile['storage']={**original['storage'],'root':p['storage']}
        except (OSError,ValueError):pass
        private_write(directory/'profile.yaml',yaml.safe_dump(profile));load_profile(directory/'profile.yaml')
        if p['token']:
            tokenfile=directory/'report-token';private_write(tokenfile,p['token']);p['config']['report_token_file']=str(tokenfile)
        private_write(directory/'device.json',json.dumps(p['config'],indent=2))
        private_write(directory/'bindings.json',json.dumps(dict(arms=p['arms'],cameras=p['cameras'],device_config=str(directory/'device.json')),indent=2))
        safe_to_configure()
        # Stop before activation so no old process can use a partially updated configuration.
        subprocess.run(['systemctl','--user','stop','yam-operator.service'],check=True,timeout=20)
        drop=Path.home()/'.config/systemd/user/yam-operator.service.d/zz-station-setup.conf'
        private_write(drop,'[Service]\nEnvironment="YAM_PROFILE='+str(directory/'profile.yaml')+'"\nEnvironment="YAM_DEVICE_CONFIG='+str(directory/'device.json')+'"\n')
        private_write(ROOT/'setup/active.json',(directory/'bindings.json').read_text())
        previous=default_profile_path()
        if previous.exists():private_write(directory/'previous-profile.yaml',previous.read_text())
        private_write(previous,(directory/'profile.yaml').read_text())
        subprocess.run(['systemctl','--user','daemon-reload'],check=True,timeout=15)
        subprocess.run(['systemctl','--user','enable','yam-operator.service'],check=True,timeout=15)
        subprocess.run(['systemctl','--user','start','yam-operator.service'],check=True,timeout=20)
        return dict(message='配置已应用。回到平台检查连接并启用工位；采集前仍须设备检查。',version=directory.name)

def _snapshot(serial):
    with LOCK:
        safe_to_configure()
        if not any(c['serial']==serial for c in discover()['cameras']):raise ValueError('请选择已连接的相机')
        import pyrealsense2 as rs
        import numpy as np
        from PIL import Image
        import io
        pipeline=rs.pipeline();cfg=rs.config();cfg.enable_device(serial);cfg.enable_stream(rs.stream.color,640,480,rs.format.rgb8,30)
        pipeline.start(cfg)
        try:
            for _ in range(8):frames=pipeline.wait_for_frames(2000)
            color=frames.get_color_frame()
            if not color:raise ValueError('相机没有返回彩色帧')
            data=io.BytesIO();Image.fromarray(np.asanyarray(color.get_data())).save(data,'JPEG',quality=80)
            return {'image':base64.b64encode(data.getvalue()).decode()}
        finally:pipeline.stop()


def _maintained(operation, value):
    with LOCK:
        marker=ROOT/'setup/maintenance'
        if marker.exists():raise ValueError('配置维护尚未结束，请联系管理员检查本机服务')
        private_write(marker,'configuration in progress')
        try:return operation(value)
        finally:marker.unlink(missing_ok=True)


def apply(body):
    return _maintained(_apply,body)


def snapshot(serial):
    return _maintained(_snapshot,serial)
