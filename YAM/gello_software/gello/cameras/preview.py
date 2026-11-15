"""Latest-frame browser preview with optional collection-state controls."""

import html
import io
import json
import secrets
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image


class CameraPreview:
    def __init__(self, rig, port=8765, lang="zh", control=None, guidance=None):
        self.guidance = guidance
        self.rig = rig
        self.control = control
        self.lang = lang
        self.token = secrets.token_urlsafe(24)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._images = {}
        self._last_request = 0.0
        self._stable_viewer_seen = 0.0
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                if self.path == f"/{owner.token}/guidance" and owner.guidance is not None:
                    if (self.headers.get("X-YAM-Control") != owner.token
                            or self.headers.get("Content-Type") != "application/json"
                            or (self.headers.get("Origin") is not None and self.headers["Origin"] != "http://" + self.headers.get("Host", ""))):
                        self.reply(403,"application/json",b'{"error":"Forbidden"}');return
                    self.connection.settimeout(2)
                    try:
                        n=int(self.headers.get("Content-Length","0"))
                        if not 0<n<=4096:raise ValueError("Invalid request size")
                        body=json.loads(self.rfile.read(n))
                        if not isinstance(body,dict):raise ValueError("Expected object")
                        result=owner.guidance.select(body['index'],body.get('opacity',.5))
                        self.reply(200,"application/json",json.dumps(result).encode())
                    except (ValueError,KeyError,OSError) as exc:self.reply(409,"application/json",json.dumps({'error':str(exc)}).encode())
                    return
                if self.path != f"/{owner.token}/control" or owner.control is None:
                    self.reply(404, "application/json", b'{"error":"Unavailable"}')
                    return
                if (
                    self.headers.get("X-YAM-Control") != owner.token
                    or self.headers.get("Content-Type") != "application/json"
                    or (
                        self.headers.get("Origin") is not None
                        and self.headers.get("Origin") != "http://" + self.headers.get("Host", "")
                    )
                ):
                    self.reply(403, "application/json", b'{"error":"Forbidden"}')
                    return
                self.connection.settimeout(2)
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 4096:
                        raise ValueError("Invalid request size")
                    data = json.loads(self.rfile.read(length))
                    if not isinstance(data, dict):
                        raise ValueError("Expected an object")
                    result = owner.control.request(data)
                    self.reply(200, "application/json", json.dumps(result).encode())
                except (ValueError, OSError) as exc:
                    self.reply(409, "application/json", json.dumps({"error": str(exc)}).encode())

            def do_GET(self):
                path = self.path.split("?", 1)[0]
                prefix = "/" + owner.token + "/"
                if path in ("/", "/preview-session", "/preview-viewer"):
                    # Bootstrap is local-only, not an unauthenticated LAN token
                    # discovery endpoint. Reject rebinding and cross-site fetches.
                    host = self.headers.get("Host", "")
                    if (not re.fullmatch(r"(?:127\.0\.0\.1|localhost)(?::[0-9]+)?", host)
                            or self.headers.get("Sec-Fetch-Site") == "cross-site"
                            or (self.headers.get("Origin") is not None and self.headers["Origin"] != "http://" + host)):
                        self.reply(403, "text/plain", b"Local preview only"); return
                    if path == "/":
                        self.reply(200, "text/html; charset=utf-8", owner.entry_page()); return
                    if self.headers.get("X-YAM-Preview") != "1":
                        self.reply(403, "text/plain", b"Same-origin preview request required"); return
                    if path == "/preview-session":
                        owner._stable_viewer_seen = time.monotonic()
                        self.reply(200, "application/json", json.dumps({"path": "/" + owner.token + "/"}).encode()); return
                    self.reply(200, "application/json", json.dumps({"active": time.monotonic()-owner._stable_viewer_seen < 5}).encode()); return
                if path == prefix + "guidance" and owner.guidance is not None:
                    self.reply(200,"application/json",json.dumps(owner.guidance.state()).encode())
                elif path.startswith(prefix + "guidance/projection/") and owner.guidance is not None:
                    try:self.reply(200,"image/png",owner.guidance.projection(int(path.rsplit('/',1)[-1])))
                    except (ValueError,IndexError):self.reply(404,"text/plain",b"Unknown projection")
                elif path == prefix:
                    self.reply(200, "text/html; charset=utf-8", owner.page())
                elif path.startswith(prefix + "stream/"):
                    name = path[len(prefix + "stream/") :]
                    if name not in owner.rig.config.cameras:
                        self.reply(404, "text/plain", b"Unknown camera")
                        return
                    self.stream(name)
                elif path.startswith(prefix + "frame/"):
                    name = path[len(prefix + "frame/") :]
                    with owner._lock:
                        owner._last_request = time.monotonic()
                        cached = owner._images.get(name)
                    if cached is None or time.monotonic() - cached[0] > 1.0:
                        self.reply(503, "text/plain", b"Frame unavailable or stale")
                    else:
                        self.reply(200, "image/jpeg", cached[1])
                else:
                    self.reply(404, "text/plain", b"Not found")

            def stream(self, name):
                # One response per view. Slow clients get the latest cached frame,
                # without an application-level frame queue or a new HTTP request.
                self.connection.settimeout(2)
                last_frame = None
                last_status = 0.0
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=yamframe")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Accel-Buffering", "no")
                    self.send_header("Referrer-Policy", "no-referrer")
                    self.send_header("X-Frame-Options", "SAMEORIGIN")
                    self.end_headers()
                    while not owner._stop.is_set():
                        now = time.monotonic()
                        with owner._lock:
                            owner._last_request = now
                            cached = owner._images.get(name)
                        if cached is not None and now - cached[0] <= 1:
                            if cached[0] != last_frame:
                                self.part("image/jpeg", cached[1])
                                last_frame = cached[0]
                        elif now - last_status >= 1:
                            # Heartbeats distinguish a reachable preview service
                            # with no fresh image from a broken network connection.
                            self.part("application/json", b'{"state":"waiting_frame"}')
                            last_status = now
                        owner._stop.wait(1 / 30)
                except OSError:
                    pass  # Disconnects and slow viewers never affect collection.

            def part(self, content_type, body):
                header = (
                    f"--yamframe\r\nContent-Type: {content_type}\r\n"
                    f"Content-Length: {len(body)}\r\n\r\n"
                ).encode("ascii")
                self.wfile.write(header + body + b"\r\n")
                self.wfile.flush()

            def reply(self, status, content_type, body):
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Referrer-Policy", "no-referrer")
                    self.send_header("X-Frame-Options", "SAMEORIGIN")
                    self.end_headers()
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_port
        self.url = f"http://127.0.0.1:{self.port}/{self.token}/"
        self.entry_url = f"http://127.0.0.1:{self.port}/"
        self._http = threading.Thread(
            target=lambda: self.server.serve_forever(poll_interval=0.1), daemon=True
        )
        self._encoder = threading.Thread(target=self._encode, daemon=True)

    def start(self):
        self._http.start()
        self._encoder.start()

    def close(self):
        self._stop.set()
        if self._http.is_alive():
            self.server.shutdown()
            self._http.join(timeout=1)
        self.server.server_close()
        if self._encoder.is_alive():
            self._encoder.join(timeout=1)

    def _encode(self):
        next_tick = time.monotonic()
        while not self._stop.wait(max(0, next_tick - time.monotonic())):
            # Include encoding time in the 30 FPS budget; never queue catch-up work.
            next_tick = time.monotonic() + 1 / 30
            # No viewers: no encoding work. Slow viewers never queue frames.
            with self._lock:
                active = time.monotonic() - self._last_request < 3
            if not active:
                continue
            try:
                frames = self.rig.latest_preview_frames()
                for name, frame in frames.items():
                    if time.monotonic() - frame.host_timestamp > 1:
                        continue
                    with self._lock:
                        cached = self._images.get(name)
                    if cached is not None and cached[0] == frame.host_timestamp:
                        continue
                    image = Image.fromarray(frame.rgb)
                    image.thumbnail((640, 480))
                    buffer = io.BytesIO()
                    image.save(buffer, format="JPEG", quality=75)
                    with self._lock:
                        self._images[name] = (frame.host_timestamp, buffer.getvalue())
            except Exception:
                # Preview failure must not interrupt capture; stale frames expire.
                continue

    def entry_page(self):
        return br"""<!doctype html><html><meta charset="utf-8"><title>YAM preview</title>
<style>html,body{margin:0;height:100%;background:#101820;color:white;font:16px system-ui}#status{padding:8px}iframe{width:100%;height:calc(100% - 40px);border:0}</style>
<div id="status">Connecting to YAM...</div><iframe id="session" allow="fullscreen"></iframe>
<script>
let sessionPath=null;
async function reconnect(){
 const status=document.getElementById('status'),frame=document.getElementById('session');
 try {
  const response=await fetch('preview-session',{headers:{'X-YAM-Preview':'1'},cache:'no-store',signal:AbortSignal.timeout(1500)});
  if(!response.ok)throw Error('offline');
  const next=await response.json();
  if(!/^\/[A-Za-z0-9_-]+\/$/.test(next.path))throw Error('invalid session');
  if(next.path!==sessionPath){sessionPath=next.path;frame.src=next.path;}
  frame.style.visibility='visible';status.textContent='YAM connected';
 }catch(e){frame.style.visibility='hidden';status.textContent='YAM disconnected - waiting for teleop restart...';}
 finally{setTimeout(reconnect,2000);}
}
reconnect();
</script></html>"""

    def page(self):
        from gello.cameras.control_panel import control_panel

        panel = control_panel(self.lang) if self.control is not None else ""
        zh = self.lang == "zh"
        title = "YAM 相机实时预览" if zh else "YAM live camera preview"
        note = (
            "仅预览 · 是否录制以终端为准 · 目标 30 FPS · 点击画面放大"
            if zh
            else "Preview only · Check terminal for recording state · Target 30 FPS · Click to enlarge"
        )
        if self.control is not None:
            note = ("目标 30 FPS · 点击画面放大 · 下方操作栏与终端同步" if zh
                    else "Target 30 FPS · Click to enlarge · Controls below sync with terminal")
        labels = {
            "main": "主视角",
            "left": "左视角",
            "right": "右视角",
            "third": "第三视角",
            "fourth": "第四视角",
        }
        cards = "".join(
            f'<article class="{"main" if name == "main" else ""}"><h2>'
            f"{html.escape(labels.get(name, name) if zh else name.upper())}</h2>"
            f'<div class="view"><img data-camera="{html.escape(name, quote=True)}" alt="">'
            "<span></span></div></article>"
            for name in self.rig.config.cameras
        )
        script = r"""
const labels=LABELS;
for (const img of document.querySelectorAll('img')) {
  const status=img.nextElementSibling;
  function show(text,dim=true){status.textContent=text;img.style.opacity=dim?'.18':'1';}
  img.onclick=()=>img.parentElement.requestFullscreen?.();
  async function connect(){
    show(labels.connecting);
    const controller=new AbortController();
    let lastBytes=performance.now(), lastFrame=0, reader;
    const watchdog=setInterval(()=>{
      if(performance.now()-lastBytes>5000)controller.abort();
      if(lastFrame && performance.now()-lastFrame>1500)show(labels.waiting);
    },250);
    try {
      const response=await fetch('stream/'+encodeURIComponent(img.dataset.camera),
                                 {cache:'no-store',signal:controller.signal});
      if(!response.ok)throw Error('HTTP '+response.status);
      reader=response.body.getReader();
      let buffer=new Uint8Array(0), length=null, type='';
      while(true){
        const {value,done}=await reader.read();
        if(done)throw Error('Stream ended');
        lastBytes=performance.now();
        const joined=new Uint8Array(buffer.length+value.length);
        joined.set(buffer);joined.set(value,buffer.length);buffer=joined;
        if(buffer.length>2097152)throw Error('Oversized stream part');
        while(true){
          if(length===null){
            let end=-1;
            for(let i=0;i+3<buffer.length;i++){
              if(buffer[i]===13 && buffer[i+1]===10 && buffer[i+2]===13 && buffer[i+3]===10){end=i;break}
            }
            if(end<0)break;
            const header=new TextDecoder().decode(buffer.slice(0,end));
            const match=header.match(/Content-Length: (\d+)/i);
            if(!match)throw Error('Missing part length');
            length=Number(match[1]);
            if(length<=0 || length>1048576)throw Error('Invalid part length');
            type=header.includes('image/jpeg')?'image':'status';
            buffer=buffer.slice(end+4);
          }
          if(buffer.length<length)break;
          const body=buffer.slice(0,length);buffer=buffer.slice(length);length=null;
          if(type==='image'){
            const url=URL.createObjectURL(new Blob([body],{type:'image/jpeg'}));
            try {
              img.src=url;
              await img.decode();
            }finally{URL.revokeObjectURL(url)}
            lastFrame=performance.now();show(labels.live,false);
          }else{
            lastFrame=0;show(labels.waiting);
          }
        }
      }
    }catch(e){show(labels.network)}
    finally {
      clearInterval(watchdog);controller.abort();
      if(reader)try{await reader.cancel()}catch(e){}
    }
    setTimeout(connect,1000);
  }
  connect();
}
""".replace(
            "LABELS",
            json.dumps(
                {
                    "connecting": "正在连接视频流" if zh else "Connecting video stream",
                    "live": "实时预览" if zh else "Live preview",
                    "waiting": "预览暂无新帧" if zh else "No fresh preview frame",
                    "network": "视频流中断 / 服务已停止，正在重连"
                    if zh
                    else "Stream interrupted / service stopped; reconnecting",
                }
            ),
        )
        guidance_panel = ""
        if self.guidance is not None:
            from gello.data_utils.real2sim_guidance import panel as render_guidance_panel
            guidance_panel = render_guidance_panel()
        return f"""<!doctype html><html lang="{"zh" if zh else "en"}"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>
<style>body{{background:#101923;color:#edf3fa;font-family:system-ui;margin:24px}}
h1{{font-size:24px}}p{{color:#aebfd0}}main{{display:grid;grid-template-columns:1fr 1fr;gap:18px;max-width:1400px;margin:auto}}
article{{background:#1b2938;border-radius:12px;overflow:hidden}}article.main{{grid-column:1/-1}}
h2{{font-size:16px;margin:12px}}.view{{position:relative;background:#000;min-height:160px;text-align:center}}
img{{display:block;width:100%;max-height:58vh;object-fit:contain;cursor:zoom-in}}span{{position:absolute;bottom:12px;left:12px;background:#172330dc;padding:6px 12px;border-radius:6px}}
.view:fullscreen img{{height:100vh;max-height:100vh}}@media(max-width:650px){{main{{grid-template-columns:1fr}}}}</style>
<h1>{title}</h1><p>{note}</p><main>{cards}</main><script>{script}</script>{panel}{guidance_panel}</html>""".encode()
