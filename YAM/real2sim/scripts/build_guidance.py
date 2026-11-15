"""Build a self-contained layout browser and collection guidance front-end."""
from pathlib import Path
import argparse, json, shutil
ROOT = Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser(); p.add_argument('--run',type=Path,default=ROOT/'runs/stack_cups_distribution'); args=p.parse_args()
data=json.loads((args.run/'layouts.json').read_text())
source=ROOT/'web/guidance.html'
(args.run/'index.html').write_text(source.read_text().replace('/*DATA*/{}', json.dumps(data, ensure_ascii=False).replace('</','<\\/')))
print(args.run/'index.html')
