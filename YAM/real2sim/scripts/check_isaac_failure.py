"""Prove that Kit cleanup cannot turn a Python failure into process success."""
import json
from pathlib import Path
import subprocess
ROOT = Path(__file__).resolve().parents[1]
out = ROOT / 'runs/isaacsim_5_1_migration'
log = out / 'intentional_failure.log'
with log.open('w') as stream:
    result = subprocess.run(['bash', str(ROOT / 'scripts/isaac_python.sh'),
                             str(ROOT / 'scripts/smoke_isaac.py'), '--fail-after-start'],
                            cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT, timeout=180)
text = log.read_text()
assert result.returncode == 1, result.returncode
assert 'REAL2SIM_APP_STARTED' in text
assert 'RuntimeError: REAL2SIM_EXPECTED_FAILURE_AFTER_START' in text
assert 'REAL2SIM_APP_CLOSED' not in text
(out / 'failure_propagation.json').write_text(json.dumps(
    {'passed': True, 'expected_exit_code': 1, 'actual_exit_code': result.returncode,
     'meaning': 'Deliberate Python exception survives SimulationApp.close().'}, indent=2))
print('PASS: intentional runtime failure exits 1 and retains the traceback')
