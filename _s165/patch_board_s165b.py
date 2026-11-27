"""s165b (Russ, 2026-11-24): the board's put-list button waits 30 minutes, not 15 -- since the 11-20 selloff
about 79 names clear the premium bar and a full list takes 15-20 minutes, so the button was cut off and recorded
nothing (no list saved 11-16 -> 11-24). One constant in helm-pg/helm_engine.py.
    python3 _s165/patch_board_s165b.py PG_TREE"""
import os, sys
p = os.path.join(sys.argv[1], "helm_engine.py"); s = open(p).read()
old = '''        r = _run_cli(["puts", "--why"], timeout=900)\n'''
new = '''        r = _run_cli(["puts", "--why"], timeout=PUTS_TIMEOUT)\n'''
if new not in s:
    assert s.count(old) == 1, "run_puts timeout line not found exactly once"
    s = s.replace(old, new)
anchor = "def run_puts() -> dict:\n"
const = ("PUTS_TIMEOUT = 30 * 60           # s165b (Russ 2026-11-24): a full list took 15-20 min once ~79 names cleared the bar; "
         "900 s cut it off unrecorded\n\n\n")
if "PUTS_TIMEOUT = " not in s:
    assert s.count(anchor) == 1
    s = s.replace(anchor, const + anchor)
open(p, "w").write(s); print("patched", p)
