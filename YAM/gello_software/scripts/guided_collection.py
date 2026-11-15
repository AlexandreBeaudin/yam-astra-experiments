"""Station-bound real2sim release selection and native YAM collector entry."""
import argparse
import json
import os
from pathlib import Path
import shlex
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gello.data_utils.real2sim_guidance import guidance_session_type, session
from gello.profile import load_profile
from gello.cameras.realsense_rig import load_camera_rig_config


def prepare(task, config_path, profile_path=None, bundle=None):
    guidance_session_type()
    from real2sim.releases import check_bundle, digest, latest
    cfg = json.loads(config_path.read_text())
    profile = load_profile(profile_path)
    if cfg['platform'] != 'yam' or cfg['station_id'] != profile.name:
        raise ValueError('工位/platform 与 YAM profile 不匹配')
    if task not in cfg['task_ids']:
        raise ValueError('任务未在工位配置 task_ids 中登记')
    def resolve(value):
        p = Path(value).expanduser()
        return p if p.is_absolute() else config_path.parent / p
    baseline = resolve(cfg['calibration_bundle'])
    cameras = load_camera_rig_config(profile.cameras.config_path)
    reference = session(baseline, cameras)
    camera_hash = digest(reference.manifest['camera'])
    if bundle is None:
        result = latest(resolve(cfg['registry']), 'yam', task, cfg['station_id'], camera_hash)
        for rejected in result['rejected']:
            print('跳过指引：' + rejected['reason'], file=sys.stderr)
        if result['release'] is None:
            raise ValueError('没有匹配工位/任务/标定的已发布指引；请先发布或显式使用 --no-guidance')
        bundle = Path(result['release']['source'])
    bundle = bundle.expanduser().resolve()
    data, _ = check_bundle(bundle, 'yam', task, camera_hash)
    if data.get('station_id', cfg['station_id']) != cfg['station_id']:
        raise ValueError('指导包工位不匹配')
    session(bundle, cameras)
    return bundle, profile


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', type=Path, default=Path.home()/'.config/yam/guidance.json')
    p.add_argument('--profile', type=Path)
    p.add_argument('--task', required=True)
    p.add_argument('--bundle', type=Path)
    p.add_argument('--no-guidance', action='store_true')
    p.add_argument('--check-only', action='store_true', help='只检查配置，不启动相机、电机或守护进程')
    p.add_argument('--publish', action='store_true', help='校验并登记 --bundle，供下次采集选择；不启动硬件')
    a = p.parse_args()
    if a.no_guidance and (a.bundle or a.publish):
        p.error('--no-guidance 不能与 --bundle/--publish 同用')
    if a.publish and (a.bundle is None or a.check_only):
        p.error('--publish 需要 --bundle，且不能与 --check-only 同用')
    try:
        profile = load_profile(a.profile)
        bundle = None
        if not a.no_guidance:
            bundle, profile = prepare(a.task, a.config.expanduser().resolve(), a.profile, a.bundle)
        if a.publish:
            from real2sim.releases import publish
            cfg = json.loads(a.config.read_text())
            registry = Path(cfg['registry']).expanduser()
            if not registry.is_absolute(): registry = a.config.resolve().parent/registry
            print(json.dumps(publish(registry, bundle, profile.name), ensure_ascii=False, indent=2))
            return
        command = [str(ROOT/'scripts/teleop'), '--profile', str(profile.path),
                   '--recording-format', 'mcap', '--control-hz', '200', '--state-hz', '200',
                   '--task-name', a.task, '--preview-port', '8765']
        if bundle:
            command += ['--guidance-bundle', str(bundle)]
            print('本会话固定指引：' + str(bundle), flush=True)
        print('启动命令：' + shlex.join(command), flush=True)
        if a.check_only:
            print('配置检查通过；未打开相机、串口或 CAN。')
            return
        print('浏览器使用终端打印的带 token 预览链接；使能仍需终端确认，录制仍需双扳机确认。', flush=True)
        os.execv(command[0], command)
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        p.exit(2, '指导采集：' + str(exc) + '\n')


if __name__ == '__main__':
    main()
