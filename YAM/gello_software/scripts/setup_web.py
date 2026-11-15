"""Loopback provisioning wizard, separate from the motor-control workbench."""
import json
import re
import secrets
import sys
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from gello import station_setup as setup
TOKEN=secrets.token_urlsafe(32)
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def local(self):
        host=self.headers.get('Host','')
        return bool(re.fullmatch(r'(localhost|127\.0\.0\.1):8772',host)) and self.headers.get('Sec-Fetch-Site')!='cross-site' and self.headers.get('Origin','http://'+host)=='http://'+host
    def reply(self,status,value,kind='application/json'):
        data=value.encode() if isinstance(value,str) else json.dumps(value,ensure_ascii=False).encode()
        self.send_response(status);self.send_header('Content-Type',kind+'; charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('X-Frame-Options','DENY');self.end_headers();self.wfile.write(data)
    def do_GET(self):
        if not self.local():return self.reply(403,{'error':'仅限本机浏览器'})
        if self.path=='/':return self.reply(200,Path(__file__).with_name('setup_ui.html').read_text().replace('__TOKEN__',json.dumps(TOKEN)),'text/html')
        if self.path=='/api/inventory':return self.reply(200,dict(inventory=setup.discover(),current=setup.current(),revision=setup.fingerprint()))
        self.reply(404,{})
    def do_POST(self):
        if not self.local() or self.headers.get('X-YAM-Setup')!=TOKEN:return self.reply(403,{'error':'请从本机向导页面操作'})
        try:
            size=int(self.headers.get('Content-Length',0))
            if not 0<size<131072:raise ValueError('请求过大或为空')
            body=json.loads(self.rfile.read(size))
            if self.path=='/api/validate':
                setup.safe_to_configure();p=setup.plan(body)
                return self.reply(200,dict(message='身份、设备映射、证书和标定文件结构检查通过。应用后仍须执行设备检查。',station_id=p['config']['station_id']))
            if self.path=='/api/apply':return self.reply(200,setup.apply(body))
            if self.path=='/api/camera':return self.reply(200,setup.snapshot(body['serial']))
            self.reply(404,{})
        except Exception as e:
            # Never echo imported credentials or YAML bodies.
            message=str(e) if isinstance(e,ValueError) else '操作失败，请检查设备连接、证书、配置文件或本机服务日志'
            self.reply(409,dict(error=message))
if __name__=='__main__':ThreadingHTTPServer(('127.0.0.1',8772),Handler).serve_forever()
