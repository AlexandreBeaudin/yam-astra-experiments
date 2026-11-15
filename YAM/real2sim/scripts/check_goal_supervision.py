"""Offline selector/browser smoke test; manual-check endpoints are retired."""
import subprocess,sys,time,urllib.request,json,urllib.error
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
proc=subprocess.Popen([sys.executable,str(ROOT/'scripts/serve_goal_supervision.py'),'--port','8768'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
try:
 for _ in range(80):
  if proc.poll() is not None:raise RuntimeError('Test server failed')
  try:
   with op.open('http://127.0.0.1:8768/api/state') as r:assert json.load(r)['mode']=='recorded_reference'
   break
  except OSError:time.sleep(.1)
 from playwright.sync_api import sync_playwright
 with sync_playwright() as p:
  b=p.chromium.launch(headless=True,args=['--no-sandbox']);page=b.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)));page.goto('http://127.0.0.1:8768');page.wait_for_function("document.getElementById('layout').value==='goal_000'&&!document.getElementById('next').disabled");assert page.locator('#prev').is_disabled();page.locator('#next').click();page.wait_for_function("document.getElementById('layout').value==='goal_001'&&!document.getElementById('next').disabled");page.locator('#prev').click();page.wait_for_function("document.getElementById('layout').value==='goal_000'&&!document.getElementById('next').disabled");assert page.locator('#submit').count()==0;assert not errors;print('Offline previous/next navigation passed; no marking UI or JS errors');b.close()
finally:proc.terminate();proc.wait(timeout=5)
