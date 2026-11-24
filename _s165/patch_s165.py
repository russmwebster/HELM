"""s165: build the spread list into an engine tree (a scratch copy; the live tree only after tests pass).
    python3 _s165/patch_s165.py ENGINE_TREE
Copies helm/spreadtool.py and helm/cli/spreads_cmd.py; registers `helm spreads`; sleeve.size says
"risks" for a spread. Refuses if a file isn't as expected."""
import os, shutil, sys
ENG = sys.argv[1]
HERE = os.path.dirname(os.path.abspath(__file__))
shutil.copyfile(os.path.join(HERE, "spreadtool.py"), os.path.join(ENG, "helm", "spreadtool.py"))
shutil.copyfile(os.path.join(HERE, "spreads_cmd.py"), os.path.join(ENG, "helm", "cli", "spreads_cmd.py"))

def edit(rel, old, new):
    p = os.path.join(ENG, rel); s = open(p).read()
    if new in s:
        return
    assert s.count(old) == 1, "%s: expected text not found exactly once" % rel
    open(p, "w").write(s.replace(old, new))

edit("helm/sleeve.py",
     '    what = "secures" if (strategy or "").upper() == "CSP" else "costs"\n',
     '    what = {"CSP": "secures", "BULL_PUT_SPREAD": "risks"}.get((strategy or "").upper(), "costs")   # s165\n')
edit("helm.py",
     "    'pmcc':      ('helm.cli.pmcc_cmd',",
     "    'spreads':   ('helm.cli.spreads_cmd',  'run', 'New bull put spreads -- ranked, sized by the most they can lose, recorded (s165)'),\n"
     "    'pmcc':      ('helm.cli.pmcc_cmd',")
edit("helm.py",
     '    ("Trading", ("advise", "puts", "calls",',
     '    ("Trading", ("advise", "puts", "spreads", "calls",')
print("patched", ENG)
