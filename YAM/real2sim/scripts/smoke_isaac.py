import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from real2sim.simulation_runtime import runtime_info, backend_label, close_app, close_app
RUNTIME = runtime_info()
import argparse
parser = argparse.ArgumentParser()
parser.add_argument('--fail-after-start', action='store_true', help='Negative test: exit must be nonzero.')
args = parser.parse_args()
from isaacsim import SimulationApp
app = SimulationApp({'fast_shutdown': True, 'headless':True,'width':640,'height':480,'anti_aliasing':0})
print('REAL2SIM_APP_STARTED',flush=True)
try:
    for _ in range(10):app.update()
    if args.fail_after_start:
        raise RuntimeError('REAL2SIM_EXPECTED_FAILURE_AFTER_START')
finally:
    close_app(app)
print('REAL2SIM_APP_CLOSED',flush=True)
