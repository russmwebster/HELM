#!/bin/bash
# helm-servers — COTS v2.0 development server (port 8766)
# FIREWALL: never touches ~/Projects/cots (v1.0)

PROJECT=~/Projects/helm

pkill -f "python3.*8766" 2>/dev/null
sleep 0.5

cd $PROJECT && python3 << 'PYEOF' &
import http.server, socketserver, json, os, subprocess
from pathlib import Path

BASE = Path.cwd()
socketserver.TCPServer.allow_reuse_address = True

ALLOWED_PREFIXES = [
    'cat ', 'grep ', 'tail ', 'head ', 'ls ', 'find ', 'echo ',
    'git add', 'git commit', 'git push', 'git status', 'git log',
    'conda run',
    '/opt/anaconda3/envs/cots/bin/python',
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
        self.send_response(200)
        self.end_headers()
    def do_PUT(self):
        path = BASE / self.path.lstrip('/')
        length = int(self.headers.get('Content-Length', 0))
        data = self.rfile.read(length)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'OK')
    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length)
        if self.path == '/exec':
            self._exec(body)
        else:
            self._write(body)
    def _exec(self, body):
        try:
            data = json.loads(body)
            cmd = data.get('cmd', '').strip()
            timeout = int(data.get('timeout', 60))
        except:
            self._error(400, 'Invalid JSON')
            return
        if not is_allowed(cmd):
            self._error(403, f'Not whitelisted: {cmd}')
            return
        try:
            r = subprocess.run(cmd, shell=True, cwd=str(BASE),
                capture_output=True, text=True, timeout=timeout,
                env={**os.environ, 'PYTHONUNBUFFERED': '1'})
            out = r.stdout + ('\n--- STDERR ---\n' + r.stderr if r.stderr.strip() else '')
            resp = json.dumps({'returncode': r.returncode, 'output': out, 'cmd': cmd}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(resp)
        except subprocess.TimeoutExpired:
            self._error(504, 'Timeout')
        except Exception as e:
            self._error(500, str(e))
    def _write(self, body):
        try:
            data = json.loads(body)
            open(data['path'], 'w').write(data['content'])
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'ok')
        except Exception as e:
            self._error(500, str(e))
    def _error(self, code, msg):
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({'error': msg}).encode())
    def log_message(self, f, *a): pass

with socketserver.TCPServer(('helm.local', 8766), Handler) as h:
    print('ready', flush=True)
    h.serve_forever()
PYEOF

sleep 0.5
echo "✅ COTS v2.0 server ready on http://helm.local:8766"
echo "   FIREWALL: v1.0 on cots.local:8765 | v2.0 on helm.local:8766"
