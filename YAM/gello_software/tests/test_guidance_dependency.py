"""Exercise dependency selection in fresh processes, without the sibling checkout."""
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]


def run_loader(extra_env=None):
    env = dict(os.environ)
    env.pop("REAL2SIM_ROOT", None)
    env["PYTHONPATH"] = str(ROOT / "gello_software")
    env.update(extra_env or {})
    return subprocess.run(
        [sys.executable, "-c", "from gello.data_utils.real2sim_guidance import "
         "guidance_session_type; import inspect; print(inspect.getfile(guidance_session_type()))"],
        cwd="/tmp", env=env, text=True, capture_output=True, timeout=10,
    )


def test_default_loads_pinned_submodule():
    result = run_loader()
    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()).resolve() == ROOT / "real2sim/src/real2sim/guidance.py"


def test_explicit_invalid_override_does_not_silently_fallback(tmp_path):
    result = run_loader({"REAL2SIM_ROOT": str(tmp_path)})
    assert result.returncode != 0
    assert "REAL2SIM_ROOT has no guidance module" in result.stderr
