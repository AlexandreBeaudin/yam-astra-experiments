"""Loopback distribution manager; never exposes robot or collector control."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
from urllib.parse import urlsplit


def make_server(catalog, port=8780, workflows=None):
    web = Path(__file__).resolve().parents[3] / 'web' / 'manager'
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, code, raw, content='text/plain; charset=utf-8'):
            self.send_response(code)
            self.send_header('Content-Type', content)
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            host = self.headers.get('Host')
            if (not re.fullmatch(r'(127\.0\.0\.1|localhost):[0-9]{1,5}', host or '') or self.headers.get('Origin') != 'http://' + host
                    or self.headers.get('X-Real2Sim-Manager') != '1'):
                return self.reply(403, b'Local same-origin access required')
            try:
                if workflows is None: raise ValueError('Editing is not configured')
                n = int(self.headers.get('Content-Length', '0'))
                if not 0 < n <= 65536: raise ValueError('Invalid body size')
                body = json.loads(self.rfile.read(n))
                if not isinstance(body, dict): raise ValueError('Expected object')
                operation = {'/api/revise':'revise','/api/render':'render','/api/render-all':'render-all','/api/generate':'generate','/api/publish':'publish'}[urlsplit(self.path).path]
                result = workflows.start(operation, body)
                return self.reply(202, json.dumps(result).encode(), 'application/json')
            except (OSError, ValueError, KeyError, TypeError) as e:
                return self.reply(400, json.dumps({'error':str(e)}, ensure_ascii=False).encode(), 'application/json')

        def do_GET(self):
            host = self.headers.get('Host')
            origin = self.headers.get('Origin')
            if not re.fullmatch(r'(127\.0\.0\.1|localhost):[0-9]{1,5}', host or '') or (origin and origin != 'http://' + host):
                return self.reply(403, b'Local same-origin access required')
            path = urlsplit(self.path).path
            if path == '/api/catalog':
                if self.headers.get('X-Real2Sim-Manager') != '1':
                    return self.reply(403, b'Same-origin API header required')
                return self.reply(200, json.dumps(workflows.enrich(catalog.overview()) if workflows else catalog.overview(), ensure_ascii=False, allow_nan=False).encode(), 'application/json')
            if path in ('/', '/app.js', '/birdview.js', '/style.css'):
                file, mime = {'/': ('index.html', 'text/html; charset=utf-8'),
                              '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                              '/birdview.js': ('birdview.js', 'text/javascript; charset=utf-8'),
                              '/style.css': ('style.css', 'text/css; charset=utf-8')}[path]
                return self.reply(200, (web / file).read_bytes(), mime)
            if re.fullmatch(r'/blobs/[0-9a-f]{64}\.(png|jpg)', path):
                target = catalog.root / path.lstrip('/')
                if target.is_file():
                    return self.reply(200, target.read_bytes(), 'image/png' if path.endswith('.png') else 'image/jpeg')
            self.reply(404, b'Not found')
    return ThreadingHTTPServer(('127.0.0.1', port), Handler)
