"""Fetch revision-pinned official robot layers from HF mirror, with no system proxy."""
import hashlib,json,os,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
lock=json.loads((ROOT/'configs/upstream_xtrainer.json').read_text())
revision=lock['asset_revision']
base='X-Trainer-URDF/urdf/x-trainer_asm-0226.SLDASM/'
files=['x_trainer.usd',base+'x-trainer_asm-0226.SLDASM.usd']+[base+'configuration/x-trainer_asm-0226.SLDASM_'+x+'.usd' for x in ['base','physics','sensor']]
env={k:v for k,v in os.environ.items() if k.lower() not in ['http_proxy','https_proxy','all_proxy']}
manifest=[]
for relative in files:
 target=ROOT/'assets/upstream/robots'/relative
 target.parent.mkdir(parents=True,exist_ok=True)
 if not target.exists():
  temp=target.with_suffix('.download')
  url=f'https://hf-mirror.com/dstx123/xtrainer-leisaac/resolve/{revision}/assets/robots/{relative}'
  subprocess.run(['curl','--fail','-L','--retry','2','--max-time','300','--noproxy','*',url,'-o',str(temp)],env=env,check=True)
  if not temp.read_bytes().startswith((b'PXR-USDC',b'#usda')):raise ValueError(f'Not a USD payload: {relative}')
  temp.replace(target)
 manifest.append({'path':relative,'bytes':target.stat().st_size,'sha256':hashlib.sha256(target.read_bytes()).hexdigest()})
(ROOT/'assets/upstream/source_manifest.json').write_text(json.dumps({'repository':lock['assets_url'],'revision':revision,'files':manifest},indent=2))
print('Verified local USD headers and recorded',len(files),'file hashes.')
