#!/usr/bin/env python3
"""Preview this station's three D405 cameras; no robot connection."""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'YAM/gello_software'))

from gello.cameras.realsense_rig import CameraRig, load_camera_rig_config
from gello.cameras.preview import CameraPreview


def main():
    config_path = ROOT / 'profiles/ps-cameras.yaml'
    config = load_camera_rig_config(config_path)
    print(f'Camera config: {config_path}', flush=True)
    for name, spec in config.cameras.items():
        print(f'  {name}: {spec.model} serial={spec.serial}', flush=True)

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


if __name__ == '__main__':
    main()
