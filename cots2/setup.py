import os, sys
base = os.path.expanduser("~/Projects/helm")
dirs = ["helm", "docs"]
for d in dirs:
    p = os.path.join(base, d)
    os.makedirs(p, exist_ok=True)
    print("created:", p)
