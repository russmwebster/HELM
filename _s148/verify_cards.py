"""s148: the position cards point back to Today and step through Today's order.

Starts the board as production starts it (python3 app.py, real engine) on a fresh DB copy,
renders Today, opens every card Today links to, and one closed position's card.
Usage: TMPDIR=/tmp python3 -B _s148/verify_cards.py [PG_DIR]   (PG_DIR: board code; default ~/mnt/helm-pg)
"""
import hashlib, html, os, re, sqlite3, subprocess, sys, time, urllib.request
HOME = os.path.expanduser("~"); ROOT = HOME + "/mnt/helm"
PG = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else HOME + "/mnt/helm-pg"
W = "/tmp/s148cards"; os.makedirs(W + "/home", exist_ok=True)
COPY = W + "/board.db"
if os.path.exists(COPY): os.remove(COPY)
sqlite3.connect("file:%s/data/helm.db?mode=ro" % ROOT, uri=True).execute("VACUUM INTO ?", (COPY,))
acct = sqlite3.connect(COPY).execute("select id from accounts").fetchone()[0]
open(W + "/home/.helm_profile", "w").write(acct)
print("board code:", PG, "| db copy:", COPY)
fails, n = [], [0]
def ok(c, m):
    n[0] += 1; print(("  ok   " if c else "  FAIL ") + m)
    if not c: fails.append(m)
def dump():
    c = sqlite3.connect(COPY); out = {}
    for (t,) in c.execute("select name from sqlite_master where type='table' order by name"):
        h = hashlib.sha256()
        for r in c.execute('select * from "%s" order by rowid' % t): h.update(repr(r).encode())
        out[t] = h.hexdigest()
    return out
before = dump()
env = dict(os.environ, HOME=W + "/home", HELM_ROOT=ROOT, HELM_DB=COPY, PORT="8772", HELM_PG_ENGINE="real", COLUMNS="200",
           PYTHONPATH="/tmp/pylib")
p = subprocess.Popen([sys.executable, "-B", "app.py"], cwd=PG, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
def get(u):
    try:
        r = urllib.request.urlopen("http://127.0.0.1:8772" + u, timeout=90); return r.status, r.read().decode()
    except Exception as e:
        return getattr(e, "code", "ERR"), str(e)
try:
    for _ in range(80):
        try: urllib.request.urlopen("http://127.0.0.1:8772/today", timeout=2); break
        except Exception: time.sleep(0.5)
    st, today = get("/today")
    ok(st == 200, "Today renders (%s)" % st)
    ids = []
    for pid in re.findall(r'href="/thesis/([^"]+)"', today):
        if pid not in ids: ids.append(pid)
    print("       Today's cards, in order:", ids)
    ok(len(ids) >= 7, "Today links every open position's card (%d)" % len(ids))
    for k, pid in enumerate(ids):
        t0 = time.time(); st, card = get("/thesis/" + pid); dt = time.time() - t0
        nav = card[:card.find("</div>", card.find("nav"))] if "nav" in card else ""
        prev = re.search(r'href="/thesis/([^"]+)"[^>]*>previous', card)
        nxt = re.search(r'href="/thesis/([^"]+)"[^>]*>next', card)
        want_prev = ids[k - 1] if k > 0 else None
        want_next = ids[k + 1] if k < len(ids) - 1 else None
        good = (st == 200 and 'href="/today"' in card and "/positions" not in card and "Traceback" not in card
                and (prev.group(1) if prev else None) == want_prev and (nxt.group(1) if nxt else None) == want_next
                and ("%d of %d on Today" % (k + 1, len(ids))) in card)
        ok(good, "%s: 200, back to Today, no /positions link, previous %s, next %s, %d of %d on Today (%.1fs)"
           % (pid, want_prev and want_prev.split("-")[0], want_next and want_next.split("-")[0], k + 1, len(ids), dt))
    c = sqlite3.connect(COPY)
    closed = c.execute("select id from positions where status='CLOSED' and book='REAL' order by closed_at desc limit 1").fetchone()
    if closed:
        st, card = get("/thesis/" + closed[0])
        ok(st == 200 and 'href="/today"' in card and "/positions" not in card and " on Today" not in card
           and not re.search(r'href="/thesis/[^"]+"[^>]*>(previous|next)', card),
           "a closed position's card (%s) renders, back to Today, no neighbours" % closed[0])
    ok('href="/positions"' in today, "Today keeps its 'Log a close (old Positions page)' link")
finally:
    p.terminate(); out = p.communicate(timeout=10)[0].decode()
    errs = [l for l in out.splitlines() if "Traceback" in l]
    ok(not errs, "no tracebacks in the board's log")
after = dump()
ok(before == after, "every table in the copy unchanged")
print("verify_cards: %d checks, %d failed" % (n[0], len(fails)))
sys.exit(1 if fails else 0)
