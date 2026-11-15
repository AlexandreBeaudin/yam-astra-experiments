"""Offline setup-helper checks, using only temporary fake station files."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import yaml

spec=importlib.util.spec_from_file_location('local_station',Path(__file__).with_name('configure_local_station.py'))
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class LocalStationTests(unittest.TestCase):
    def test_dry_run_apply_and_mapping_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'SOURCE_SNAPSHOT.json').write_text('{}')
            runner=root/'integrations/inspect_robots/astra_run.py';runner.parent.mkdir(parents=True)
            original="if socket.gethostname().lower().split('.')[0] != 'rhos-nuc-1':\n    raise RuntimeError('station operations must run on rhos-nuc-1')\n"
            runner.write_text(original)
            for side,channel in [('left','can_yam_b'),('right','can_yam_a')]:
                (root/f'{side}.yaml').write_text(yaml.safe_dump({'robot':{'channel':channel}}))
            (root/'cameras.yaml').write_text('{}')
            profile=root/'station.yaml'
            profile.write_text(yaml.safe_dump(dict(schema_version=1,arms={'left':'left.yaml','right':'right.yaml'},cameras={'config':'cameras.yaml'},network={'host':'127.0.0.1','hardware_port':6001,'safety_port':6002})))
            before=profile.read_bytes()
            with patch.object(module,'ROOT',root), patch.object(module.socket,'gethostname',return_value='lab-yam-2'), contextlib.redirect_stdout(io.StringIO()):
                module.configure(profile,'lab-yam-2')
                self.assertEqual(runner.read_text(),original)
                self.assertFalse((root/'profiles/shurui-mcap.yaml').exists())
                module.configure(profile,'lab-yam-2',True)
                self.assertIn("!= 'lab-yam-2'",runner.read_text())
                self.assertEqual(runner.with_suffix('.py.bak').read_text(),original)
                self.assertEqual(profile.read_bytes(),before)
                p=yaml.safe_load((root/'profiles/shurui-mcap.yaml').read_text())
                self.assertEqual(p['arms']['right'],str(root/'right.yaml'))
                with self.assertRaises(ValueError):module.configure(profile,'wrong-host',True)
                (root/'right.yaml').write_text('robot: {channel: can_yam_b}')
                with self.assertRaises(ValueError):module.configure(profile,'lab-yam-2',True)
                (root/'SOURCE_SNAPSHOT.json').unlink()
                with self.assertRaises(RuntimeError):module.configure(profile,'lab-yam-2',True)


if __name__=='__main__':unittest.main()
