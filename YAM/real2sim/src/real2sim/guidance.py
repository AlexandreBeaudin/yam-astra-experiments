"""Platform-independent guidance bundle selection and immutable episode snapshots."""
import base64
import copy
import hashlib
import json
import re
import threading
from pathlib import Path

class GuidanceSession:
    def __init__(self, bundle, platform=None, camera_size=None):
        self.bundle=Path(bundle).resolve()
        if (self.bundle/'RECALLED.json').exists():
            raise ValueError('Guidance bundle recalled: see RECALLED.json; regenerate and validate before use')
        self.manifest=json.loads((self.bundle/'manifest.json').read_text())
        task=self.manifest['task']
        if platform is not None:
            if task.get('platform')!=platform:raise ValueError('Guidance bundle platform mismatch')
            if self.manifest.get('calibration_status')!='verified':raise ValueError('Platform calibration must be verified before live projection')
        if camera_size is not None and list(camera_size)!=self.manifest['camera']['image_size']:
            raise ValueError('Guidance camera resolution differs from saved camera stream')
        self.ids=[];self._snapshots=[];self._index=0;self._opacity=.5;self._lock=threading.Lock()
        for layout in self.manifest['layouts']:
            ident=layout['id']
            if not re.fullmatch(r'[a-zA-Z0-9_-]+',ident) or ident in self.ids:raise ValueError('Invalid/duplicate layout ID')
            raw=(self.bundle/(ident+'.png')).read_bytes()
            from PIL import Image
            import io
            im=Image.open(io.BytesIO(raw))
            if im.mode!='RGBA' or list(im.size)!=self.manifest['camera']['image_size']:raise ValueError('Projection must be RGBA at calibrated resolution')
            self.ids.append(ident)
            self._snapshots.append(dict(schema_version=2,split=layout.get('split'),split_group_id=layout.get('split_group_id'),platform=task.get('platform'),task_id=task['task_id'],layout_id=ident,layout=layout,camera=self.manifest['camera'],required_materials=layout.get('required_materials',self.manifest.get('required_materials',[])),calibration_version=self.manifest.get('calibration_version'),task_rules=self.manifest.get('task_rules',{}),deployment_status=self.manifest.get('deployment_status'),limitations=self.manifest.get('limitations',[]),projection_sha256=hashlib.sha256(raw).hexdigest(),projection_png_base64=base64.b64encode(raw).decode('ascii'),bundle_sha256=hashlib.sha256((self.bundle/'manifest.json').read_bytes()).hexdigest(),placement_validation='pending_first_frame_review'))
        if not self.ids:raise ValueError('Guidance bundle has no layouts')
    def select(self, index, opacity=.5):
        if type(index)!=int or not 0<=index<len(self.ids):raise ValueError('Invalid guidance index')
        if type(opacity) not in (int,float) or not 0<=opacity<=1:raise ValueError('Invalid opacity')
        with self._lock:self._index=index;self._opacity=opacity
        return self.state()
    def snapshot(self):
        with self._lock:
            s=copy.deepcopy(self._snapshots[self._index]);s['opacity']=self._opacity
        return s
    def state(self):
        s=self.snapshot();s.pop('projection_png_base64')
        return dict(index=self.ids.index(s['layout_id']),count=len(self.ids),**s)
    def projection(self,index):
        if type(index)!=int or not 0<=index<len(self.ids):raise ValueError('Invalid guidance index')
        return base64.b64decode(self._snapshots[index]['projection_png_base64'])
    @staticmethod
    def export_snapshot(snapshot, destination, first_frame=None):
        from PIL import Image
        import io
        destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
        s=copy.deepcopy(snapshot);raw=base64.b64decode(s.pop('projection_png_base64'),validate=True)
        if hashlib.sha256(raw).hexdigest()!=s['projection_sha256']:raise ValueError('Snapshot projection digest mismatch')
        (destination/'guidance_projection.png').write_bytes(raw)
        (destination/'placement_guidance.json').write_text(json.dumps(s,ensure_ascii=False,indent=2))
        if first_frame is not None:
            image=Image.open(first_frame).convert('RGBA');overlay=Image.open(io.BytesIO(raw)).convert('RGBA')
            if image.size!=overlay.size:raise ValueError('First frame resolution does not match calibration')
            overlay.putalpha(overlay.getchannel('A').point(lambda a:int(a*s['opacity'])))
            Image.alpha_composite(image,overlay).convert('RGB').save(destination/'guidance_overlay_first_frame.jpg')
