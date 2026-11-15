"""MCAP provenance is pinned per episode, not changed by later strategy selection."""
import json
from gello.data_utils.platform_guidance import PlatformGuidance
from gello.data_utils.mcap_recording import McapEpisodeWriter
from gello.data_utils.mcap_tools import messages

def test_platform_reference_is_copied_into_each_episode(tmp_path):
    context = tmp_path / 'context.json'
    initial = dict(source='rdp', guidance_kind='reference', calibration_status='unverified_reference', batch_uuid='batch', strategy_id='layout-1', image_base64='test-image')
    context.write_text(json.dumps(initial))
    writer = McapEpisodeWriter(tmp_path/'data', 'RDP-test', 200, image_encoding='raw+zstd')
    writer.guidance = PlatformGuidance(context)
    try:
        writer.start()
        writer.record('/action/accepted', dict(position=[0]*14), writer._epoch)
        context.write_text(json.dumps(dict(initial, strategy_id='layout-2')))
        first = writer.commit('success')
        recorded = list(messages(first, '/guidance/initial'))
        assert len(recorded) == 1
        assert recorded[0][2] == initial
        writer.start()
        writer.record('/action/accepted', dict(position=[0]*14), writer._epoch)
        second = writer.commit('fail')
        assert list(messages(second, '/guidance/initial'))[0][2]['strategy_id'] == 'layout-2'
    finally:
        writer.close()
