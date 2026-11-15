"""Hardware-free integration demo using the production UI, state machine and MCAP writer."""
import argparse
import json
import math
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gello.cameras.preview import CameraPreview
from gello.data_utils.collection_session import run_collection_session
from gello.data_utils.mcap_recording import McapEpisodeWriter
from gello.data_utils.real2sim_guidance import guidance_session_type
from gello.data_utils.web_control import CollectionControl


def make_bundle(root):
    """Deterministic illustrative pixel layouts; NOT real2sim/physics validation."""
    root.mkdir(parents=True, exist_ok=True)
    layouts = []
    rng = np.random.default_rng(42)
    for i in range(6):
        x, y = int(rng.integers(130, 330)), int(rng.integers(170, 330))
        name = f'mock-{i+1:02d}'
        layouts.append(dict(id=name, mock=True, object_pixel=[x,y], target_pixel=[470,240]))
        im = Image.new('RGBA', (640,480))
        d = ImageDraw.Draw(im)
        d.rectangle((x-30,y-30,x+30,y+30), outline=(255,200,40,255), width=5)
        d.rectangle((420,190,520,290), outline=(50,240,150,255), width=5)
        d.text((x-30,y-50), 'PLACE BLOCK', fill='white')
        d.text((420,170), 'TARGET TRAY', fill='white')
        im.save(root / f'{name}.png')
    (root/'manifest.json').write_text(json.dumps(dict(task=dict(platform='yam', task_id='MOCK-DEMO'),
        mock=True, calibration_status='verified', calibration_version='SYNTHETIC-ONLY',
        limitations=['Synthetic pixel fixture, no physical calibration or reachability validation'],
        camera=dict(image_size=[640,480],serial='MOCK-main',saved_image_rotation_deg=0), layouts=layouts),indent=2))


class MockRig:
    def __init__(self, guide):
        self.guide = guide
        self.config = SimpleNamespace(cameras={n:None for n in ('main','left','right')})
    def latest_preview_frames(self):
        now = time.monotonic()
        result = {}
        layout = self.guide.snapshot()['layout']
        for index, name in enumerate(self.config.cameras):
            im = Image.new('RGB',(640,480),(30+index*20,45,60))
            d = ImageDraw.Draw(im)
            d.rectangle((65,110,590,420),fill=(95,100,105),outline='white',width=2)
            x,y = layout['object_pixel']
            x += int(12*math.sin(now)) + index*15
            d.rectangle((x-25,y-25,x+25,y+25),fill=(220,130,45))
            d.rectangle((425,195,515,285),outline=(65,220,150),width=8)
            d.text((20,20),f'MOCK ONLY | {name.upper()} | frame {int(now*30)}',fill='white')
            d.text((20,45),'Synthetic images / no camera or robot connected',fill='white')
            result[name]=SimpleNamespace(rgb=np.asarray(im),host_timestamp=now,frame_number=int(now*30))
        return result


class MockEnvironment:
    control_rate_hz = 200
    def __init__(self):
        self.q = np.zeros(14)
        self.q[[6,13]]=1
    def get_obs(self):
        return dict(joint_positions=self.q.copy(),joint_velocities=np.zeros(14))
    def step(self, action):
        time.sleep(1/self.control_rate_hz)
        self.q=np.asarray(action).copy()
        return self.get_obs()


class MockAgent:
    def __init__(self, control): self.control=control
    def act(self, obs):
        q=obs['joint_positions'].copy()
        q[[6,13]]=0 if self.control.phase=='waiting_for_trigger' and self.control.trigger_stage=='press' else 1
        if self.control.phase=='recording':
            q[0]=q[7]=.08*math.sin(time.monotonic())
        return q


class MockWriter(McapEpisodeWriter):
    def __init__(self, root, rig):
        super().__init__(root,'MOCK-DEMO',200,image_encoding='h264',
            camera_metadata={n:dict(serial='MOCK-'+n,width=640,height=480,fps=30,rotation=0) for n in rig.config.cameras})
        self.metadata.update(mock=True,state_sampling='synthetic_per_control_tick')
        self.mock_rig=rig
    def start(self):
        self.next_frame=0
        return super().start()
    def append(self, observation, action, timestamp, leader_action=None, **kwargs):
        super().append(observation,action,timestamp,leader_action,**kwargs)
        stamp=time.time_ns()
        for i,side in enumerate(('left','right')):
            self.record(f'/{side}/state',dict(position=observation['joint_positions'][i*7:i*7+7],velocity=np.zeros(7),effort=np.zeros(7),mock=True),stamp)
        if time.monotonic()>=self.next_frame:
            self.next_frame=time.monotonic()+1/30
            for name,frame in self.mock_rig.latest_preview_frames().items():
                self.record(f'/cameras/{name}/rgb',dict(frame_number=frame.frame_number,serial='MOCK-'+name,mock=True),stamp,image=frame.rgb)


class MockPreview(CameraPreview):
    def page(self):
        return super().page().decode().replace('<h1>','<h2 style="color:#ffce60">MOCK 演示：合成图像与状态，不连接硬件；扳机自动模拟</h2><h1>').replace('准备下一条（机械臂将移动）','准备下一条（模拟对齐和扳机）').encode()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=Path.home()/'.local/share/yam/mock-guidance')
    p.add_argument('--port',type=int,default=8766)
    a=p.parse_args(); a.root.mkdir(parents=True,exist_ok=True)
    bundle=a.root/'bundle';make_bundle(bundle)
    guide=guidance_session_type()(bundle,platform='yam')
    rig=MockRig(guide); writer=MockWriter(a.root/'data',rig);writer.guidance=guide
    control=CollectionControl('MOCK-DEMO',writer.next_episode_index)
    preview=MockPreview(rig,port=a.port,control=control,guidance=guide)
    def emit(event):
        control.observe(event)
        with (a.root/'events.jsonl').open('a') as f:f.write(json.dumps(event,default=str)+'\n')
    def worker():
        try:
            run_collection_session(env=MockEnvironment(),agent=MockAgent(control),robot=None,writer=writer,
                commands=control,emit=emit,auto_start=False,activate_control=lambda:None,
                enter_hold=lambda reason:dict(state='safe_hold',hold_confirmed=True),wait_for_triggers=True)
        except Exception as exc:
            emit(dict(type='safety_fault',message=str(exc)))
            raise
        finally: writer.close()
    preview.start();threading.Thread(target=worker,daemon=True).start()
    (a.root/'url.txt').write_text(preview.url+'\n');print(preview.url,flush=True)
    try:
        while True:time.sleep(1)
    except KeyboardInterrupt:pass
    finally:control.put(dict(type='shutdown'));preview.close()

if __name__=='__main__':main()
