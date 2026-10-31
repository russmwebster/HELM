#!/usr/bin/env python3
"""_s142/verify_net_pnl.py -- net of fees from 10/24 (s142).

On a fresh VACUUM INTO copy of helm.db (the live one is only read), through the real CLI:
  N1 `helm fills --file <year-to-date download> --apply --yes` records the missing
     fee rows of every trade in the sleeve period (open now, or closed from 10/24):
     every leg's opening row and each bought-back leg's closing row; nothing else
     changes (only broker_fills and helm_events)
  N2 every one of those positions then reads "net" (no row missing), its net =
     gross - its fees, and the fees equal the download's own rows for it
  N3 the sleeve: realized = gross - fees paid from 10/24; the board/Details line says
     "net of $X fees (gross $Y)"
  N4 a rerun proposes nothing; an OLDER download imported afterwards, holding XLE's
     earlier copy of its row ($0.09 instead of $0.11), is recorded, and the fees keep
     the newest download's copy -- counted once, at $0.11
  N5 a position closed before 10/24 stays gross, labelled
  perturbation (python3 -B, patched copy): the one-row-per-trade rule switched off ->
     XLE's fee counts twice and the check fails

  cd ~/Projects/helm && python3 _s142/verify_net_pnl.py
"""
import glob, json, os, shutil, sqlite3, subprocess, sys, tempfile, time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LIVE = Path(os.environ.get("VERIFY_LIVE_DB", REPO / "data" / "helm.db"))
DL = Path(os.environ.get("VERIFY_DOWNLOADS", Path.home() / "Downloads"))
TMP = Path(tempfile.mkdtemp(prefix="helm-s142n-"))
ok_all = True


def check(c, m):
    global ok_all
    print(("  PASS  " if c else "  FAIL  ") + m)
    ok_all &= bool(c)


def cli(db, args, root=REPO):
    time.sleep(1.1)
    env = dict(os.environ, HELM_DB=str(db), HELM_ROOT=str(root), COLUMNS="300", HELM_DOWNLOADS=str(TMP / "nodl"))
    r = subprocess.run([sys.executable, "-B", str(root / "helm.py"), "fills"] + args, capture_output=True, text=True,
                       env=env, timeout=170)
    return r.stdout + r.stderr


def py(db, code, root=REPO):
    env = dict(os.environ, HELM_DB=str(db), HELM_ROOT=str(root), PYTHONPATH=str(root))
    r = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True, env=env, cwd=str(root))
    return json.loads(r.stdout.strip().splitlines()[-1]) if r.returncode == 0 else {"err": r.stderr[-500:]}


NET = ("import json,sqlite3,os,helm.netpnl as N,helm.sleeve as S\n"
       "c=sqlite3.connect(os.environ['HELM_DB']); c.row_factory=sqlite3.Row\n"
       "ids=[r[0] for r in c.execute(\"SELECT id FROM positions WHERE book='REAL' AND (status='OPEN' OR substr(closed_at,1,10)>='2026-10-24')\")]\n"
       "old=c.execute(\"SELECT id FROM positions WHERE book='REAL' AND status!='OPEN' AND substr(closed_at,1,10)<'2026-10-24' LIMIT 1\").fetchone()[0]\n"
       "b=S.book()\n"
       "print(json.dumps({'pos':{i:N.position(c,i) for i in ids},'old':N.position(c,old),'fees':N.fees_since(c),"
       "'share':b['share'],'realized':b['realized'],'gross':b['realized_gross'],'basis':b['basis']}))")


def download_rows_fees(path, ira_known_db):
    """Per HELM position: the download's own fee total for its legs' open/close rows (independent tally)."""
    code = ("import json,sqlite3,os,sys; from helm import brokerfills as B, fillimport as FI\n"
            "c=sqlite3.connect(os.environ['HELM_DB']); c.row_factory=sqlite3.Row\n"
            "e=B.read_export(%r, FI.stored_ira_hash(c))\n"
            "out={}\n"
            "legs=c.execute(\"SELECT l.*,p.ticker,p.tags FROM legs l JOIN positions p ON p.id=l.position_id WHERE p.book='REAL' AND "
            "l.option_type IN ('PUT','CALL') AND (p.status='OPEN' OR substr(p.closed_at,1,10)>='2026-10-24')\").fetchall()\n"
            "for l in legs:\n"
            "  if 'split_from:' in (l['tags'] or ''): continue\n"
            "  sym='-%%s%%s%%s%%s%%s%%g'%%(l['ticker'],l['expiration'][2:4],l['expiration'][5:7],l['expiration'][8:10],'P' if l['option_type']=='PUT' else 'C',l['strike'])\n"
            "  for f in e.fills:\n"
            "    if f.symbol.strip()!=sym: continue\n"
            "    if (f.event=='OPEN' and f.as_of==l['open_date'][:10]) or (f.event=='CLOSE' and l['close_date'] and f.as_of==l['close_date'][:10] and (l['close_price'] or 0)>0):\n"
            "      out[l['position_id']]=round(out.get(l['position_id'],0)+(f.commission or 0)+(f.fees or 0),2)\n"
            "print(json.dumps(out))" % str(path))
    return py(ira_known_db, code)


def fresh(name):
    db = TMP / (name + ".db")
    s = sqlite3.connect("file:%s?mode=ro" % LIVE, uri=True); s.execute("VACUUM INTO ?", (str(db),)); s.close()
    return db


def diff(a, b):
    c = sqlite3.connect(b); c.execute("ATTACH ? AS o", (str(a),))
    out = set()
    for (t,) in c.execute("SELECT name FROM main.sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
        if not c.execute("SELECT 1 FROM o.sqlite_master WHERE name=?", (t,)).fetchone():
            out.add(t); continue
        try:
            n = c.execute('SELECT (SELECT COUNT(*) FROM (SELECT * FROM main."%s" EXCEPT SELECT * FROM o."%s")) + '
                          '(SELECT COUNT(*) FROM (SELECT * FROM o."%s" EXCEPT SELECT * FROM main."%s"))' % (t, t, t, t)).fetchone()[0]
        except sqlite3.OperationalError:
            n = 1            # its columns changed (s142: broker_fills.downloaded_at) -- a change
        if n:
            out.add(t)
    c.close()
    return out


def scenario(root, tag):
    r = {}
    ytd = max(glob.glob(str(DL / "Accounts_History*.csv")), key=lambda f: len(open(f, encoding="utf-8-sig").read()))
    hist = sorted(glob.glob(str(DL / "History_for_Account*.csv")), key=os.path.getmtime)
    (TMP / "nodl").mkdir(exist_ok=True)
    db = fresh("N" + tag); pre = TMP / ("N%s-pre.db" % tag); shutil.copy(db, pre)
    r["before"] = py(db, NET, root)
    r["out1"] = cli(db, ["--file", ytd, "--apply", "--yes"], root)
    r["tables"] = diff(pre, db)
    r["after"] = py(db, NET, root)
    r["dl_fees"] = download_rows_fees(ytd, db)
    r["rerun"] = cli(db, ["--file", ytd], root)
    r["old_out"] = cli(db, ["--file", hist[0], "--apply", "--yes"], root) if hist else ""
    r["after_old"] = py(db, NET, root)
    return r


def judge(r, tag=""):
    check("To record -- opening fees" in r["out1"] and "Written:" in r["out1"],
          tag + "N1 the year-to-date import records the missing fee rows")
    check(r["tables"] <= {"broker_fills", "helm_events", "helm_dividends"},
          tag + "N1 only broker_fills and helm_events change (+ the new, empty dividends table): %s" % sorted(r["tables"]))
    a = r["after"]
    bad = {k: v["label"] for k, v in a["pos"].items() if v["label"] != "net"}
    check(not bad, tag + "N2 every position in the sleeve period reads 'net' (%d positions)%s"
          % (len(a["pos"]), "" if not bad else ": " + str(bad)))
    off = {k: (v["fees"], r["dl_fees"].get(k, 0.0)) for k, v in a["pos"].items()
           if "split_from" not in k and abs((v["fees"] or 0) - r["dl_fees"].get(k, 0.0)) > 0.011}
    check(not off, tag + "N2 each position's fees equal the download's own rows for it%s" % ("" if not off else ": " + str(off)))
    gn = [k for k, v in a["pos"].items() if v["gross"] is not None and abs(v["net"] - (v["gross"] - v["fees"])) > 0.005]
    check(not gn, tag + "N2 net = gross - fees on every closed position")
    check(abs(a["realized"] - (a["gross"] - a["fees"])) < 0.005 and abs(a["share"] - (300000 + a["realized"])) < 0.005
          and a["basis"].startswith("net of"),
          tag + "N3 the sleeve: realized $%.2f = gross $%.2f - fees $%.2f; '%s'" % (a["realized"], a["gross"], a["fees"], a["basis"]))
    check("Nothing to write" in r["rerun"], tag + "N4 a rerun proposes nothing")
    xa = [v for k, v in a["pos"].items() if k.startswith("XLE-CSP")]
    xb = [v for k, v in r["after_old"]["pos"].items() if k.startswith("XLE-CSP")]
    check(xa and xb and abs(xa[0]["fees"] - xb[0]["fees"]) < 0.005 and abs(a["fees"] - r["after_old"]["fees"]) < 0.005,
          tag + "N4 after an older download with XLE's revised row, XLE's fees still count once ($%s -> $%s; sleeve fees $%s -> $%s)"
          % (xa[0]["fees"] if xa else "?", xb[0]["fees"] if xb else "?", a["fees"], r["after_old"]["fees"]))
    o = a["old"]
    check(o["label"].startswith("gross -- fees not applied") and o["fees"] is None, tag + "N5 a trade closed before 10/24 stays gross, labelled")


def main():
    global ok_all
    print("code: %s   temp: %s\n" % (REPO, TMP))
    r = scenario(REPO, "")
    judge(r)
    print("   (sleeve before %s -> after %s; fees from 10/24 $%s)" % (r["before"].get("share"), r["after"]["share"], r["after"]["fees"]))
    good = ok_all
    print("\nperturbation: one row per trade switched off")
    pr = TMP / "perturbed"
    shutil.copytree(REPO / "helm", pr / "helm", ignore=shutil.ignore_patterns("__pycache__", "*.bak*"))
    shutil.copy(REPO / "helm.py", pr / "helm.py"); (pr / "data").mkdir()
    f = pr / "helm" / "netpnl.py"; s = f.read_text()
    old = "        keep[k] = r          # the newest download's copy wins (a row with no download time counts as oldest)"
    assert s.count(old) == 1
    f.write_text(s.replace(old, "        keep[r['fill_key']] = r"))
    saved, ok_all = ok_all, True
    judge(scenario(pr, "p"), tag="[perturbed] ")
    caught = not ok_all
    ok_all = saved
    print("\n  %s  perturbation caught" % ("PASS" if caught else "FAIL"))
    print("\nALL PASS" if (good and caught) else "\nNOT PASSED -- send me the output")
    sys.exit(0 if (good and caught) else 1)


if __name__ == "__main__":
    main()
