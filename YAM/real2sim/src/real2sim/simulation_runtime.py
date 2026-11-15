"""Version contract shared by all simulation entry points; no Kit import here."""
import importlib.metadata
import json
from pathlib import Path
import sys
from packaging.version import Version


def runtime_info():
    config = json.loads((Path(__file__).resolve().parents[2] /
                         'configs/simulation_runtime.json').read_text())
    actual = importlib.metadata.version('isaacsim')
    # NVIDIA wheels use a fourth zero component (5.1.0.0).
    if Version(actual) != Version(config['version']):
        raise RuntimeError('Isaac Sim version mismatch: expected %s, found %s. '
                           'Use scripts/isaac_python.sh.' % (config['version'], actual))
    python = '%d.%d' % sys.version_info[:2]
    if python != config['python']:
        raise RuntimeError('Isaac Sim requires Python ' + config['python'])
    return {'simulator': config['simulator'], 'version': actual,
            'python': python, 'physics': config['physics']}


def backend_label():
    return 'Isaac Sim %s RTX' % runtime_info()['version']


def close_app(app):
    """Preserve failures before Kit's fast shutdown exits the process.

    Full extension teardown in the tested 5.1 PhysX runtime segfaults; retain
    vendor-default fast shutdown. On failure, print and flush the Python error
    and exit nonzero rather than letting native shutdown mask it as success.
    These are standalone CLI processes; the OS releases GPU/process resources.
    """
    error = sys.exc_info()
    if error[0] is not None:
        import os
        import traceback
        traceback.print_exception(*error)
        sys.stdout.flush()
        sys.stderr.flush()
        code = error[1].code if isinstance(error[1], SystemExit) else 1
        os._exit(code if isinstance(code, int) and code != 0 else 1)
    sys.stdout.flush()
    sys.stderr.flush()
    app.close()
