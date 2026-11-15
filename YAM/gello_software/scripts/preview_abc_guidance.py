"""Explicit live-camera-only test of an ABC-derived simulated calibration. No motor imports."""
import argparse
import json
from pathlib import Path
import signal
import sys
import threading

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from gello.cameras.preview import CameraPreview
from gello.cameras.realsense_rig import CameraRig,load_camera_rig_config
from gello.data_utils.real2sim_guidance import guidance_session_type


def test_guidance(bundle,config):
    manifest=json.loads((bundle/'manifest.json').read_text())
    if (bundle/'RECALLED.json').exists():raise ValueError('Guidance is recalled')
    if manifest.get('calibration_status')!='simulated' or manifest.get('simulation_only') is not True:
        raise ValueError('This test entry only accepts explicitly simulated bundles')
    if manifest['source']['repository']!='https://github.com/amazon-far/abc':raise ValueError('Not an ABC fixture')
    cam=config.cameras['main'];spec=manifest['camera']
    if (spec['serial'],spec['image_size'],spec['saved_image_rotation_deg'])!=(cam.serial,[cam.width,cam.height],cam.rotation):raise ValueError('Camera identity, resolution or rotation mismatch')
    # Standalone preview only: do not call the production verified-calibration adapter.
    return guidance_session_type()(bundle,camera_size=(cam.width,cam.height))

class TestPreview(CameraPreview):
    def page(self):
        html=super().page().decode()
        html=html.replace('YAM 相机实时预览','摆放指导测试').replace('仅预览 · 是否录制以终端为准 · 目标 30 FPS · 点击画面放大','相机实时预览 · 当前不录制 · 点击画面放大')
        html=html.replace('</head>', '<style>button{background:#24364a;color:#eff6ff;border:1px solid #60788e;border-radius:8px;padding:10px 16px;margin:5px;cursor:pointer}button:disabled{opacity:.4}input[type=range]{accent-color:#69b8ff}</style></head>')
        html=html.replace('<h1>', '<p style="padding:14px;background:#493919;color:#ffe49a">模拟标定测试 · ABC 标准工位 · 仅相机预览，不使能或控制机械臂。网格间距 10 cm，彩色轮廓为 5 cm 测试方块。</p><h1>',1)
        html=html.replace('按投影摆放，再按原扳机流程开始；录完手动还原后切换指引。无需标点。目标写入 MCAP，实际摆放待首帧核对。','切换布局并调整透明度，检查主视角中的桌面网格和目标轮廓。当前不录制数据，不控制机械臂。')
        html=html.replace('</html>', '''<script>const panel=document.querySelector('#guide-next').closest('section');document.querySelector('main').before(panel);</script></html>''')
        return html.encode()

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--bundle',type=Path,required=True);p.add_argument('--camera-config',type=Path,default=Path.home()/'.config/yam/realsense_bimanual.yaml');p.add_argument('--port',type=int,default=8765);a=p.parse_args()
    config=load_camera_rig_config(a.camera_config);guide=test_guidance(a.bundle,config)
    stop=threading.Event();signal.signal(signal.SIGTERM,lambda *_:stop.set());signal.signal(signal.SIGINT,lambda *_:stop.set())
    def commands():
        for line in sys.stdin:
            if line.strip()=='stop':stop.set();break
    threading.Thread(target=commands,daemon=True).start()
    rig=CameraRig(config);preview=None
    try:
        rig.start();preview=TestPreview(rig,port=a.port,guidance=guide);preview.start()
        print('YAM_WEB_EVENT '+json.dumps(dict(type='guidance_preview_ready',url=preview.entry_url,task='ABC-D405-PLACEMENT-TEST')),flush=True)
        stop.wait()
    finally:
        if preview:preview.close()
        rig.close()

if __name__=='__main__':main()
