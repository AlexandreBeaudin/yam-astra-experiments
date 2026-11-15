import unittest
from unittest.mock import patch
from real2sim.simulation_runtime import runtime_info


class RuntimeContractTests(unittest.TestCase):
    def test_nvidia_four_component_version(self):
        with patch('importlib.metadata.version', return_value='5.1.0.0'), \
                patch('sys.version_info', (3, 11, 16)):
            self.assertEqual(runtime_info()['version'], '5.1.0.0')

    def test_wrong_simulator_rejected(self):
        for version in ('4.5.0.0', '5.0.0', '6.1.0', '5.1.0rc1'):
            with self.subTest(version=version), \
                    patch('importlib.metadata.version', return_value=version):
                with self.assertRaisesRegex(RuntimeError, 'version mismatch'):
                    runtime_info()

    def test_wrong_python_rejected(self):
        with patch('importlib.metadata.version', return_value='5.1.0.0'), \
                patch('sys.version_info', (3, 10, 12)):
            with self.assertRaisesRegex(RuntimeError, 'Python 3.11'):
                runtime_info()
