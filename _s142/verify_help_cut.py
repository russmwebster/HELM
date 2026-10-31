#!/usr/bin/env python3
"""_s142/verify_help_cut.py -- `helm help` cut to today's commands, the rest behind `helm legacy` (s142).

  H1 the command table is unchanged: every command maps to the same module and
     function as before (helm.py.bak-20261031-s142b) -- nothing stops running
  H2 `helm help` lists exactly the 17 main commands, `helm legacy` the other 23;
     every command is in one list and only one
  H3 read-only commands give byte-for-byte the same output through a pipe, old vs
     new helm.py, on one database copy (sleeve, book, advise, workflow, guide, a
     retired command, an unknown command)
  H4 the legacy notice appears only at a terminal (never on a pipe: the board parses
     close/resell output)
  H5 `helm restart trial` is refused before launchctl; pg and all are still targets

  cd ~/Projects/helm && python3 _s142/verify_help_cut.py
"""
import os, re, shutil, sqlite3, subprocess, sys, tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LIVE = Path(os.environ.get("VERIFY_LIVE_DB", REPO / "data" / "helm.db"))
TMP = Path(tempfile.mkdtemp(prefix="helm-s142h-"))
OLD = REPO / "helm.py.bak-20261031-s142b"
ok_all = True


def check(c, m):
    global ok_all
    print(("  PASS  " if c else "  FAIL  ") + m)
    ok_all &= bool(c)


def table(path):
    ns = {"__file__": str(REPO / "helm.py")}
    exec(compile(Path(path).read_text().split("def main():")[0], str(path), "exec"), ns)
    return ns


def run(entry, args, db):
    env = dict(os.environ, HELM_DB=str(db), HELM_ROOT=str(REPO), COLUMNS="160")
    r = subprocess.run([sys.executable, "-B", str(entry)] + args, capture_output=True, text=True, env=env,
                       timeout=60, cwd=str(REPO), stdin=subprocess.DEVNULL)
    return r.returncode, r.stdout, r.stderr


def main():
    new, old = table(REPO / "helm.py"), table(OLD)
    a = {k: v[:2] for k, v in new["COMMANDS"].items()}
    b = {k: v[:2] for k, v in old["COMMANDS"].items()}
    check(a == b, "H1 every command maps to the same module and function as before (%d)" % len(a))
    M = [c for _, n in new["MAIN"] for c in n]; L = [c for _, n in new["LEGACY"] for c in n]
    check(len(M) == 17 and len(L) == 23 and not set(M) & set(L) and set(M) | set(L) == set(a),
          "H2 17 main + 23 legacy = every command, each in one list")
    db = TMP / "h.db"
    s = sqlite3.connect("file:%s?mode=ro" % LIVE, uri=True); s.execute("VACUUM INTO ?", (str(db),)); s.close()
    old_entry = TMP / "helm_old.py"          # outside the repo: nothing is left behind
    shutil.copy(OLD, old_entry)
    try:
        # the old entry point must import helm from REPO: same sys.path insert as helm.py (parent dir) -> patch
        t = old_entry.read_text().replace("sys.path.insert(0, str(Path(__file__).parent))",
                                          "sys.path.insert(0, %r)" % str(REPO))
        old_entry.write_text(t)
        rc, out, _ = run(REPO / "helm.py", ["help"], db)
        shown = set(re.findall(r"^\s+([a-z-]+)\s{2,}", out, re.M))
        check(rc == 0 and shown == set(M) and "helm legacy" in out, "H2 helm help shows exactly the 17 (%d shown)" % len(shown))
        rc, out, _ = run(REPO / "helm.py", ["legacy"], db)
        shown = set(re.findall(r"^\s+([a-z-]+)\s{2,}", out, re.M))
        check(rc == 0 and shown == set(L) and "2026-12-01" in out, "H2 helm legacy shows exactly the 23 (%d shown)" % len(shown))
        for args in (["sleeve"], ["book"], ["advise"], ["workflow"], ["guide"], ["activity"], ["nosuchcmd"]):
            o = run(old_entry, args, db); n = run(REPO / "helm.py", args, db)
            check(o == n, "H3 helm %s: same exit code, stdout and stderr as before (exit %s, %d chars)"
                  % (" ".join(args), n[0], len(n[1])))
        rc, out, err = run(REPO / "helm.py", ["workflow"], db)
        check("legacy command" not in out + err, "H4 no legacy notice on a pipe")
        if shutil.which("script"):
            env = dict(os.environ, HELM_DB=str(db), HELM_ROOT=str(REPO))
            r = subprocess.run(["script", "-qec", "%s -B %s workflow" % (sys.executable, REPO / "helm.py"), "/dev/null"],
                               capture_output=True, text=True, env=env, timeout=60, cwd=str(REPO))
            check("legacy command, due to go on 2026-12-01" in r.stdout, "H4 the notice appears at a terminal")
        else:
            print("  (H4 at a terminal: no `script` here to fake one -- check by hand: helm workflow)")
    finally:
        pass
    sys.path.insert(0, str(REPO))
    import helm.cli.server_cmd as S
    check("trial" not in S.TARGETS and "pg" in S.TARGETS and "server" in S.TARGETS
          and not any(v == "trial" for v in S.ALIASES.values()), "H5 trial is gone from the restart targets; pg and the server stay")
    calls = []
    real = subprocess.run
    subprocess.run = lambda *a, **k: calls.append(a) or real(*a, **k)
    sys.argv = ["helm restart", "trial"]
    try:
        rc = S.run()
    except SystemExit as e:
        rc = e.code
    subprocess.run = real
    check(rc not in (0, None) and not any("launchctl" in str(c) for c in calls),
          "H5 helm restart trial is refused (exit %s) before launchctl is called" % rc)
    print("\nALL PASS" if ok_all else "\nNOT PASSED -- send me the output")
    sys.exit(0 if ok_all else 1)


if __name__ == "__main__":
    main()
