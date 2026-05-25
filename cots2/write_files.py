import json, sys, os
from pathlib import Path

BASE = Path.home() / 'Projects' / 'helm'
manifest = json.loads(sys.stdin.read())
for rel_path, content in manifest.items():
    path = BASE / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    print(f'wrote: {rel_path}')
print('done')
