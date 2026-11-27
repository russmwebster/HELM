"""s166: render the board's pages on a database copy (test client), print status + size + a hash. Usage: pages.py DB"""
import os, sys, hashlib, re, site
db = sys.argv[1]
home = "/tmp/s166/home"; os.makedirs(home, exist_ok=True)
open(os.path.join(home, ".helm_profile"), "w").write("fidelity_9e60c8")
os.environ.update(HOME=home, HELM_DB=db, HELM_ROOT=os.path.expanduser("~/mnt/helm") if False else os.environ["HELM_ROOT"], PORT="8791", PYTHONHASHSEED="0")
sys.path[:0] = [os.environ["PG"], os.environ["HELM_ROOT"]]
os.chdir(os.environ["PG"])
import app as A
c = A.app.test_client()
for path in ("/today", "/book", "/positions", "/outcomes", "/reference", "/thesis/GOOGL-COVERED_CALL-20261123-CDB860", "/thesis/GOOGL-CSP-20261015-CED969"):
    r = c.get(path); body = r.get_data(as_text=True)
    norm = re.sub(r'clip[\w-]*-?\w+|id="c[0-9a-f]+"|url\(#[^)]+\)', "", body)
    print("%-52s %s %7d %s err=%d googl=%d" % (path, r.status_code, len(body), hashlib.sha256(norm.encode()).hexdigest()[:10],
          len(re.findall(r"Traceback|Internal Server Error|could not be built", body)), body.count("GOOGL")))
