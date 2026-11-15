import importlib.util
import json
from pathlib import Path

import pytest
from mcap.writer import Writer
from mcap.reader import make_reader

spec = importlib.util.spec_from_file_location('repair', Path(__file__).parents[1] / 'scripts/repair_arm_sides.py')
repair = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repair)


def fixture_session():
    return {'schema_version': 2, 'arm_names': ['left', 'right'],
            'arm_configs': {s: {'robot': {'channel': c}, 'agent': {'port': serial}}
                            for s, (c, serial) in repair.OLD.items()},
            'cameras': {s: {'serial': serial} for s, serial in repair.SERIALS.items()}}


def test_whole_file_round_trip(tmp_path):
    source, dest = tmp_path / 'old.mcap', tmp_path / 'new.mcap'
    with source.open('wb') as f:
        w = Writer(f)
        w.start(profile='yam.multirate.v1')
        schema = w.register_schema('test', 'jsonschema', b'{}')
        for i, topic in enumerate(sorted(repair.TOPICS)):
            ch = w.register_channel(topic, 'json', schema)
            if topic == '/session':
                data = fixture_session()
            elif topic.startswith('/action/'):
                data = {'position': list(range(14)), 'command_id': 8}
                if topic.endswith('accepted'):
                    data['requested_position'] = list(range(100, 114))
                    data['limited'] = True
            else:
                data = {'arbitrary_exact_bytes_evidence': topic}
            w.add_message(ch, 100+i, repair.encode({'source_time_ns': 50+i, 'record_time_ns': 100+i, 'data': data}), publish_time=50+i, sequence=i)
        w.add_metadata('quality', {'counts': json.dumps({'/left/state': 3, '/right/state': 7}),
                                  'rates': json.dumps({'/left/state': {'measured_hz': 261}, '/right/state': {'measured_hz': 254}})})
        w.finish()
    repair.rewrite(source, dest)
    assert repair.verify(source, dest) == len(repair.TOPICS)
    with dest.open('rb') as f:
        r = make_reader(f)
        samples = {c.topic: json.loads(m.data)['data'] for _, c, m in r.iter_messages()}
        meta = next(r.iter_metadata()).metadata
    assert samples['/left/state']['arbitrary_exact_bytes_evidence'] == '/right/state'
    assert samples['/cameras/left/rgb']['arbitrary_exact_bytes_evidence'] == '/cameras/left/rgb'
    assert samples['/action/accepted']['position'] == list(range(7, 14)) + list(range(7))
    assert samples['/action/accepted']['requested_position'] == list(range(107, 114)) + list(range(100, 107))
    assert samples['/session']['arm_configs']['left']['robot']['channel'] == 'can_follower_l'
    assert json.loads(meta['counts'])['/left/state'] == 7
    with pytest.raises(ValueError, match='Already repaired'):
        repair.check_session(samples['/session'])


def test_reject_unknown_fields_and_mapping():
    with pytest.raises(ValueError):
        repair.transform('/action/accepted', repair.encode({'data': {'position': list(range(14)), 'new_arm_field': []}}))
    with pytest.raises(ValueError):
        repair.swap_vector(list(range(28)))
    s = fixture_session()
    s['arm_configs']['left']['robot']['channel'] = 'unverified'
    with pytest.raises(ValueError):
        repair.check_session(s)


def test_config_preserves_complete_control_pairs(tmp_path):
    import yaml
    directory = tmp_path / 'arms'
    directory.mkdir()
    configs = fixture_session()['arm_configs']
    configs['left']['agent']['dynamixel_config'] = {'gripper_config': [7, 157, 198]}
    configs['right']['agent']['dynamixel_config'] = {'gripper_config': [7, 158, 200]}
    for side, cfg in configs.items():
        (directory / f'yam_{side}.yaml').write_text(yaml.safe_dump(cfg))
    repair.repair_configs(directory, tmp_path / 'backup')
    assert yaml.safe_load((directory / 'yam_left.yaml').read_text()) == configs['right']
    assert yaml.safe_load((directory / 'yam_right.yaml').read_text()) == configs['left']
    with pytest.raises(ValueError):
        repair.repair_configs(directory, tmp_path / 'backup2')


def test_apply_preserves_original_and_rejects_stale_plan(tmp_path):
    root = tmp_path / 'data'
    root.mkdir()
    test_whole_file_round_trip(root)
    source = root / 'old.mcap'
    original_bytes = source.read_bytes()
    plan = {'root': str(root), 'entries': [{'path': 'old.mcap', 'sha256': repair.sha(source)}]}
    backup = tmp_path / 'backup'
    repair.apply(plan, backup)
    assert (backup / 'old.mcap').read_bytes() == original_bytes
    assert source.read_bytes() != original_bytes
    assert repair.verify(backup / 'old.mcap', source) == len(repair.TOPICS)
    assert len((backup / 'completed.jsonl').read_text().splitlines()) == 1
    with pytest.raises(ValueError, match='Source changed'):
        repair.apply(plan, tmp_path / 'second-backup')
    assert not (tmp_path / 'second-backup').exists()


def test_inventory_skips_new_correctly_configured_recordings(tmp_path):
    s = fixture_session()
    s['start_time_ns'] = 10
    configs = s['arm_configs']
    s['arm_configs'] = {'left': configs['right'], 'right': configs['left']}
    path = tmp_path / 'new-recording.mcap'
    with path.open('wb') as f:
        w = Writer(f)
        w.start()
        schema = w.register_schema('test', 'jsonschema', b'{}')
        channel = w.register_channel('/session', 'json', schema)
        w.add_message(channel, 10, repair.encode({'data': s}), publish_time=10)
        w.finish()
    result = repair.inventory(tmp_path, 0)
    assert result['entries'] == []
    assert result['skipped'][0]['reason'] == 'already correct physical mapping'
