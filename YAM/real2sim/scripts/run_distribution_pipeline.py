"""One command: propose -> validate -> PhysX settle -> RTX -> guidance page."""
import argparse, subprocess, sys, time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--count',type=int);p.add_argument('--seed',type=int,default=20260914)
p.add_argument('--proposals',type=Path);p.add_argument('--run',type=Path,default=None)
p.add_argument('--train-count',type=int);p.add_argument('--validation-count',type=int)
a=p.parse_args();a.run=(a.run or ROOT/'runs'/('stack_cups_distribution_'+time.strftime('%Y%m%d_%H%M%S'))).resolve()
generate=[str(ROOT/'.venv/bin/python'),str(ROOT/'scripts/generate_layouts.py'),'--seed',str(a.seed),'--output',str(a.run)]
if a.count is not None:generate+=['--count',str(a.count)]
if a.train_count is not None:generate+=['--train-count',str(a.train_count)]
if a.validation_count is not None:generate+=['--validation-count',str(a.validation_count)]
if a.proposals:generate+=['--proposals',str(a.proposals.resolve())]
commands=[generate,['bash',str(ROOT/'scripts/isaac_python.sh'),str(ROOT/'scripts/render_layouts.py'),'--run',str(a.run)],
          [str(ROOT/'.venv/bin/python'),str(ROOT/'scripts/build_guidance.py'),'--run',str(a.run)]]
for command in commands:subprocess.run(command,cwd=ROOT,check=True)
print('Ready:',a.run/'index.html')
