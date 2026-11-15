"""Browser UX regression with intercepted APIs; never sends hardware commands.
Run using the real2sim utility Python with Playwright installed.
"""
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from playwright.sync_api import sync_playwright, expect
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'scripts'))
from gello.cameras.preview import CameraPreview
from gello.data_utils.web_control import CollectionControl
from operator_ui import enhance_collector

control=CollectionControl('UI-TEST',1);control.observe(dict(type='ready'))
rig=SimpleNamespace(config=SimpleNamespace(cameras={'main':None,'left':None,'right':None}),latest_preview_frames=lambda:{})
preview=CameraPreview(rig,port=0,control=control)
# No preview server or real device is started.
page_html=enhance_collector(preview.page().decode())
preview.server.server_close()
state=dict(revision=0,busy=False,running=False,message='请选择设备检查',notice_level='info',check_passed=False,current_action=None,phase='idle',task='UI-TEST',events=[],output=[],safety=dict(state='offline',hold_confirmed=False))
actions=[]
def respond(route):
    path=route.request.url.split('8770',1)[-1]
    status=200;body='';kind='application/json'
    if path=='/':body=(ROOT/'scripts/operator_ui.html').read_text().replace('TOKEN',json.dumps('test'));kind='text/html'
    elif path=='/collector':body='<iframe id="session" src="/test/" style="height:100%;width:100%"></iframe>';kind='text/html'
    elif path=='/test/':body=page_html;kind='text/html'
    elif path=='/api/status':body=json.dumps(state)
    elif path=='/api/action':
        d=route.request.post_data_json;actions.append(d['action'])
        if d['action']=='check':state.update(check_passed=True,message='✓ 启动前检查通过',notice_level='success',current_action='check',revision=1)
        elif d['action']=='enable':state.update(current_action='enable',revision=2,running=True,phase='ready',safety=dict(state='safe_hold',hold_confirmed=True))
        body='{}'
    elif path=='/test/control':
        try:
            d=route.request.post_data_json;result=control.request(d);body=json.dumps(result)
            if d['action']=='start':
                control.get_nowait();control.observe(dict(type='sync_started'));state['phase']='syncing'
            elif d['action']=='finish':
                control.get_nowait();control.observe(dict(type='awaiting_label'));state['phase']='review'
            elif d['action'] in ('success','fail','discard'):
                control.get_nowait();control.observe(dict(type='next_ready',episode_index=2));state['phase']='ready'
                state['events']=[dict(type='episode_committed',outcome=d['action'],path='/mock/test.mcap')]
        except ValueError as exc:status=409;body=json.dumps(dict(error=str(exc)))
    else:status=503;body='{}'
    route.fulfill(status=status,body=body,content_type=kind)

with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,args=['--no-sandbox'])
    context=browser.new_context(viewport={'width':1440,'height':1050});context.route('http://127.0.0.1:8770/**',respond)
    page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)));page.on('dialog',lambda d:d.accept())
    page.goto('http://127.0.0.1:8770/');expect(page.locator('#primary')).to_have_text('检查设备')
    page.locator('#primary').click();expect(page.locator('#primary')).to_have_text('上使能');expect(page.locator('#report-dialog')).to_be_visible();page.locator('#report-confirm').click();expect(page.locator('#report-dialog')).not_to_be_visible();page.wait_for_timeout(900);expect(page.locator('#report-dialog')).not_to_be_visible();page.locator('#primary').click()
    expect(page.locator('#primary')).to_have_text('开始采集');expect(page.locator('#primary')).to_be_enabled(timeout=10000)
    expect(page.locator('#access')).to_have_text('操作已自动连接')
    # A second tab cannot take over the first tab's lease.
    other=context.new_page();other.goto('http://127.0.0.1:8770/');expect(other.locator('#access')).to_have_text('另一页面正在操作',timeout=10000);expect(other.locator('#primary')).to_be_disabled();other.close()
    page.locator('#primary').click();expect(page.locator('#next-title')).to_have_text('正在慢速对齐 · 尚未录制');expect(page.locator('#primary')).to_be_disabled()
    control.observe(dict(type='waiting_for_trigger'));state['phase']='waiting_for_trigger'
    expect(page.locator('#next-title')).to_have_text('等待扳机确认 · 尚未录制')
    control.observe(dict(type='episode_started',episode_index=1));state['phase']='recording'
    expect(page.locator('#primary')).to_have_text('结束采集');expect(page.locator('#primary')).to_be_enabled();page.locator('#primary').click()
    expect(page.locator('[data-collect=success]')).to_be_visible();page.locator('[data-collect=success]').click()
    expect(page.locator('#result-list')).to_contain_text('已保存');expect(page.locator('#result-list')).to_contain_text('/mock/test.mcap');expect(page.locator('#primary')).to_have_text('开始采集')
    # Text output is not interpreted as HTML.
    state.update(message='错误：<img src=x onerror=alert(1)>',notice_level='error')
    expect(page.locator('#message')).to_contain_text('<img');assert page.locator('#message img').count()==0
    expect(page.locator('#report-dialog')).to_be_visible()
    page.locator('#report-confirm').click()
    for width in (1440, 1024, 760, 390):
        page.set_viewport_size({'width':width,'height':900})
        page.wait_for_timeout(350)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'),width
        primary=page.locator('#primary').bounding_box();finish=page.locator('.finish-actions').bounding_box()
        assert primary and finish and (primary['x']+primary['width']<=finish['x'] or primary['y']+primary['height']<=finish['y']), (width,primary,finish)
        assert finish['y']+finish['height']<=900,(width,finish)
    assert not errors,errors
    assert actions==['check','enable'],actions
    page.screenshot(path=str(Path.home()/'.local/state/yam-operator/ux-mock-flow.png'))
    browser.close()
print('PASS: ordered workflow, auto-claim, second-tab exclusion, align/trigger stages, save receipt, escaped logs; no hardware commands')
