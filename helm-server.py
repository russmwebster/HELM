#!/usr/bin/env python3
"""
helm-server.py — HELM development server (port 8766)
Standalone — stays in foreground, managed by launchd.
FIREWALL: never touches ~/Projects/cots (v1.0)
"""

import http.server, socketserver, json, os, subprocess
from pathlib import Path

BASE = Path(__file__).parent.resolve()
HOME = Path.home()

# Make bridge-spawned (/exec) subprocesses resolve the helm-env interpreter
# and the helm wrapper: the non-login /exec shell loads neither the conda env
# nor the user shell alias. Children inherit this via env={**os.environ,...}.
_ENV_BIN = "/opt/anaconda3/envs/helm/bin"
os.environ["PATH"] = f"{BASE / 'bin'}:{_ENV_BIN}:" + os.environ.get("PATH", "")
socketserver.TCPServer.allow_reuse_address = True

ALLOWED_PREFIXES = [
    'cat ', 'grep ', 'tail ', 'head ', 'ls ', 'find ', 'echo ',
    'mkdir ', 'rm ', 'cp ', 'mv ', 'touch ', 'wc ', 'sort ',
    'git add', 'git commit', 'git push', 'git status', 'git log', 'git diff',
    'python3 ', 'sqlite3 ',
    '/opt/anaconda3/envs/helm/bin/python3',
    '/opt/anaconda3/envs/cots/bin/python',
    'conda run',
    'helm ',
    'bash -c',
]

def is_allowed(cmd):
    return any(cmd.strip().startswith(p) for p in ALLOWED_PREFIXES)

class Handler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, PUT, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200); self.end_headers()

    def do_GET(self):
        if self.path.startswith('/health'):
            self._health()
            return
        if self.path.startswith('/russ-scan'):
            self._russ_scan()
            return
        if self.path.startswith('/read?path='):
            self._read(self.path[11:])
        else:
            super().do_GET()

    def _health(self):
        import urllib.parse as _up
        try:
            import sys as _sys
            if str(BASE) not in _sys.path:
                _sys.path.insert(0, str(BASE))
            import importlib
            import helm.db as _db
            import helm.health as _H
            importlib.reload(_H)
            q = _up.urlparse(self.path).query
            ticker = _up.parse_qs(q).get('ticker', [None])[0]
            conn = _db.get_conn()
            try:
                out = _H.render(conn, ticker)
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
            body = out.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception:
            import traceback, html as _htmlmod
            tb = traceback.format_exc()
            page = ("<!doctype html><html><body>"
                    "<h2>HELM health error</h2><pre>"
                    + _htmlmod.escape(tb) + "</pre></body></html>")
            body = page.encode('utf-8')
            self.send_response(500)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def do_PUT(self):
        path = BASE / self.path.lstrip('/')
        length = int(self.headers.get('Content-Length', 0))
        data = self.rfile.read(length)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        self.send_response(200); self.end_headers()
        self.wfile.write(b'OK')

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length)
        if self.path == '/exec':   self._exec(body)
        elif self.path == '/read': self._read(json.loads(body).get('path',''))
        elif self.path == '/russ-scan/commit': self._russ_commit(body)
        else:                      self._write(body)

    def _read(self, path_str):
        try:
            p = Path(path_str.replace('~', str(HOME))).expanduser()
            if not p.is_absolute(): p = BASE / p
            if not p.exists(): self._error(404, f'Not found: {path_str}'); return
            if p.is_dir():
                items = [{'name':i.name,'type':'dir' if i.is_dir() else 'file','size':i.stat().st_size if i.is_file() else 0} for i in sorted(p.iterdir())]
                resp = json.dumps({'path':str(p),'items':items}).encode()
            else:
                try: content = p.read_text(encoding='utf-8')
                except UnicodeDecodeError: content = p.read_text(encoding='latin-1')
                resp = json.dumps({'path':str(p),'content':content,'size':p.stat().st_size}).encode()
            self.send_response(200)
            self.send_header('Content-Type','application/json'); self.end_headers()
            self.wfile.write(resp)
        except Exception as e: self._error(500, str(e))

    def _exec(self, body):
        try:
            data = json.loads(body)
            cmd = data.get('cmd','').strip()
            timeout = int(data.get('timeout', 15))
        except: self._error(400, 'Invalid JSON'); return
        if not is_allowed(cmd): self._error(403, f'Not whitelisted: {cmd}'); return
        try:
            r = subprocess.run(cmd, shell=True, cwd=str(BASE),
                capture_output=True, text=True, timeout=timeout,
                env={**os.environ, 'PYTHONUNBUFFERED': '1'})
            out = r.stdout + ('\n--- STDERR ---\n' + r.stderr if r.stderr.strip() else '')
            resp = json.dumps({'returncode':r.returncode,'output':out,'cmd':cmd}).encode()
            self.send_response(200)
            self.send_header('Content-Type','application/json'); self.end_headers()
            self.wfile.write(resp)
        except subprocess.TimeoutExpired: self._error(504, 'Timeout')
        except Exception as e: self._error(500, str(e))

    def _write(self, body):
        try:
            data = json.loads(body)
            path = BASE / data['path']
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(data['content'])
            self.send_response(200); self.end_headers()
            self.wfile.write(b'ok')
        except Exception as e: self._error(500, str(e))

    def _russ_scan(self):
        try:
            import sys as _sys
            if str(BASE) not in _sys.path:
                _sys.path.insert(0, str(BASE))
            import importlib
            import helm.db as _db
            import helm.russ_scan as _RS
            importlib.reload(_RS)
            conn = _db.get_conn()
            try:
                out = _RS.render(conn, self.path)
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
            payload = out.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(payload)
        except Exception:
            import traceback
            self._error(500, traceback.format_exc())

    def _russ_commit(self, body):
        try:
            import sys as _sys
            if str(BASE) not in _sys.path:
                _sys.path.insert(0, str(BASE))
            import importlib
            import helm.db as _db
            import helm.russ_scan as _RS
            importlib.reload(_RS)
            conn = _db.get_conn()
            try:
                resp = _RS.commit(conn, json.loads(body))
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
            data = json.dumps(resp).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(data)
        except Exception:
            import traceback
            self._error(500, traceback.format_exc())

    def _error(self, code, msg):
        self.send_response(code)
        self.send_header('Content-Type','application/json'); self.end_headers()
        self.wfile.write(json.dumps({'error':msg}).encode())

    def log_message(self, f, *a): pass

if __name__ == '__main__':
    print('✅ HELM server ready on http://helm.local:8766', flush=True)
    with socketserver.ThreadingTCPServer(('helm.local', 8766), Handler) as server:
        server.serve_forever()
