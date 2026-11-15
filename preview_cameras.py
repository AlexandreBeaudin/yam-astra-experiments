import time
from pathlib import Path
import yaml
from gello.cameras.realsense_rig import CameraRig, load_camera_rig_config
from gello.cameras.preview import CameraPreview

profile = yaml.safe_load(Path('profiles/shurui-mcap.yaml').read_text())
config = load_camera_rig_config(Path(profile['cameras']['config']))
rig = CameraRig(config, depth_enabled=False)
preview = None
try:
    rig.start()
    preview = CameraPreview(rig, port=8765, lang='en')
    preview.start()
    print('Camera preview: http://127.0.0.1:8765/', flush=True)
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    pass
finally:
    if preview is not None:
        preview.close()
    rig.close()
