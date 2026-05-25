
from pathlib import Path
BASE = Path.home() / 'Projects' / 'helm'

content = open(BASE / 'cots2' / 'screen_cmd_content.txt').read()
(BASE / 'helm' / 'cli' / 'screen.py').write_text(content)
print('written')
