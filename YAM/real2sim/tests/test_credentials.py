import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from real2sim.credentials import save_credentials, load_credentials

class CredentialsTests(unittest.TestCase):
    def test_persistence_permissions_and_replacement(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ,{},clear=True):
            path=Path(d)/'private/feishu.json'
            save_credentials('dummy-id','dummy-secret',path)
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777,0o700)
            self.assertEqual(load_credentials(path),('dummy-id','dummy-secret'))
            save_credentials('new-id','new-secret',path)
            self.assertEqual(load_credentials(path),('new-id','new-secret'))
            with self.assertRaises(ValueError):save_credentials('','',path)
            self.assertEqual(load_credentials(path),('new-id','new-secret'))
            path.chmod(0o644)
            with self.assertRaises(ValueError):load_credentials(path)
    def test_environment_pair_precedence(self):
        with patch.dict(os.environ,{'FEISHU_APP_ID':'env-id','FEISHU_APP_SECRET':'env-secret'},clear=True):
            self.assertEqual(load_credentials('/does/not/exist'),('env-id','env-secret'))
        with patch.dict(os.environ,{'FEISHU_APP_ID':'partial'},clear=True):
            with self.assertRaises(ValueError):load_credentials('/does/not/exist')

if __name__=='__main__':unittest.main()
