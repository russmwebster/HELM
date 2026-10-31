#!/usr/bin/env python3
"""_s141/tidy_step1.py -- operations review, step 1: tidy HELM's folder.

Default is a DRY RUN: the whole plan is applied to a throwaway copy of the
repo (code + .git, no data/) and the copy's resulting `git status` is shown.
Nothing in the real folders changes.  --apply does it for real (asks y/N).

What it does (Russ, 2026-10-31):
  * Old top-level scripts and notes -> archive/root/ (tracked, `git mv`) or
    archive/local/root/ (untracked scratch, plain move, stays out of git).
  * _sNNN session folders -> archive/sessions/ (`git mv`); their untracked files
    -> archive/local/sessions/.  _s122 stays (fetch_px.py + px/, the long price
    history) and so does _s141 (this session).
  * archive/'s June contents -> archive/local/2026-07/.
  * .gitignore: `archive/` -> `/archive/local/`; `/sample_mktdata.py` dropped
    (the sampler job runs it -- it goes into git); `/data/archive/`,
    `/_s122/px/` and `/_s122/lc_backtest_results.json` added.  Per-pattern ignores unchanged.
  * The two top-level plists -> launchd/.
  * .bak files in helm and helm-pg deleted, except s138 and later
    (session >= 138 or dated >= 2026-10-28).
  * data/: the 15 pre-repair copies, and the helm.db.bak-* copies dated
    2026-10-25 or earlier (before the leg-readings fix), MOVE to
    ~/Backups/helm/pre-repair/.  The newest three helm.db.bak-* stay.  The
    other helm.db.bak-* (after the fix) are deleted.  .fuse_hidden* files are
    deleted only if nothing holds them open (lsof).

Never moves: helm.py, helm-server.py, helm-servers.sh, paper_exit_agent.py,
sample_mktdata.py, ORIENTATION.md, README.md, .gitignore.
Refuses: inside the snapshot windows on a weekday, with a git index.lock, with
any staged or modified tracked file, or if a destination already exists.

Usage (on the Mac):
  cd ~/Projects/helm && python3 _s141/tidy_step1.py            # dry run
  cd ~/Projects/helm && python3 _s141/tidy_step1.py --apply    # for real
"""
import argparse, datetime as dt, os, re, shutil, subprocess, sys, tempfile
from pathlib import Path

PRODUCTION = {"helm.py", "helm-server.py", "helm-servers.sh",
              "paper_exit_agent.py", "sample_mktdata.py"}
KEEP_ROOT = PRODUCTION | {"ORIENTATION.md", "README.md", ".gitignore", ".DS_Store"}
KEEP_DIRS = {".git", "helm", "tools", "bin", "launchd", "data", "docs", "logs",
             "_records", "_s122", "_s141", "Claude outputs", "_to_delete",
             "archive", "__pycache__"}
PLISTS = ["com.helm.mktsampler.plist", "com.helm.snapshot.daily.plist"]
WINDOWS = [((10, 0), (10, 35)), ((12, 30), (13, 5)), ((15, 15), (15, 50))]
BAK_RE = re.compile(r"(\.bak|\.pre_|_bak)")
KEEP_SESSION, KEEP_DATE = 138, "20261028"
LEGFIX_DATE = "20261025"          # helm.db.bak-* on/before this predate the fix

def git(repo, *args, check=True):
    r = subprocess.run(["git", "--no-optional-locks", *args], cwd=repo,
                       capture_output=True, text=True)
    if check and r.returncode:
        sys.exit(f"git {' '.join(args)} failed:\n{r.stderr}")
    return r.stdout

def tracked(repo):
    return set(git(repo, "ls-files", "-z").split("\0")) - {""}

def untracked_visible(repo, under):
    out = git(repo, "ls-files", "-z", "--others", "--exclude-standard", "--", under)
    return [p for p in out.split("\0") if p]

def is_bak(name):
    return bool(BAK_RE.search(name))

def bak_keep(name):
    suf = name[BAK_RE.search(name).start():]
    sess = [int(x) for x in re.findall(r"(?<![0-9a-z])s(\d{2,3})(?![0-9])", suf)]
    dates = re.findall(r"(20\d{6})", suf)
    return (sess and max(sess) >= KEEP_SESSION) or (dates and max(dates) >= KEEP_DATE)

def find_baks(root):
    out = []
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x != ".git" and not
                   (Path(d) == Path(root) and x == "data")]
        out += [Path(d) / f for f in files if is_bak(f)]
    return sorted(out)

class Plan:
    def __init__(self, repo, pg, backups):
        self.repo, self.pg, self.backups = Path(repo), Path(pg) if pg else None, Path(backups)
        self.gitmv, self.mv, self.delete, self.dbmove, self.notes = [], [], [], [], []

    def build(self):
        R = self.repo
        T = tracked(R)
        tdirs = {p.split("/")[0] for p in T if "/" in p}
        for name in PRODUCTION | {"ORIENTATION.md", "README.md", ".gitignore"}:
            if not (R / name).exists():
                sys.exit(f"REFUSED: {name} is missing -- this is not the folder I expect.")
        # 1. archive/'s existing contents (untracked) -> archive/local/2026-07/
        if (R / "archive").is_dir():
            for e in sorted((R / "archive").iterdir()):
                if e.name in ("local", "root", "sessions") or e.name.startswith("TIDY-"):
                    continue
                self.mv.append((e, R / "archive/local/2026-07" / e.name, "archive's June contents"))
        # 2. .bak files (helm, excluding data/; helm-pg)
        for root in [R] + ([self.pg] if self.pg else []):
            for p in find_baks(root):
                if not bak_keep(p.name):
                    self.delete.append((p, "old .bak (before s138)"))
        dead = {p for p, _ in self.delete}
        # 3. top-level files
        for e in sorted(R.iterdir()):
            if e.is_dir() or e.name in KEEP_ROOT or e in dead or is_bak(e.name):
                # a .bak that is kept stays beside its file
                continue
            if e.name in PLISTS:
                self.gitmv.append((e, R / "launchd" / e.name, "plist with the others"))
            elif e.name in T:
                self.gitmv.append((e, R / "archive/root" / e.name, "old top-level file (tracked)"))
            else:
                self.mv.append((e, R / "archive/local/root" / e.name, "old top-level scratch"))
        # 4. top-level folders
        for e in sorted(R.iterdir()):
            if not e.is_dir() or e.name in KEEP_DIRS:
                continue
            if not re.fullmatch(r"_s\d{2,3}", e.name):
                self.notes.append(f"left alone (not a session folder): {e.name}/")
                continue
            if e.name in tdirs:
                self.gitmv.append((e, R / "archive/sessions" / e.name, "session folder (has tracked files)"))
            else:
                self.mv.append((e, R / "archive/local/sessions" / e.name, "session folder (untracked)"))
        # 5. data/
        D = R / "data"
        if D.is_dir():
            pre = sorted(list(D.glob("helm-*.db")) + list(D.glob("helm.pre-*.db")))
            for p in pre:
                self.dbmove.append((p, self.backups / p.name, "pre-repair copy"))
            baks = sorted([p for p in D.glob("helm.db.bak-*")
                           if not p.name.endswith(("-shm", "-wal"))],
                          key=lambda p: p.stat().st_mtime, reverse=True)
            for p in baks[:3]:
                self.notes.append(f"kept (newest three): data/{p.name}")
            for p in baks[3:]:
                d = re.findall(r"(20\d{6})", p.name)
                if d and max(d) <= LEGFIX_DATE:
                    self.dbmove.append((p, self.backups / p.name, "before the leg-readings fix"))
                else:
                    self.delete.append((p, "database backup after the fix, not newest three"))
            for p in D.glob("helm.db.bak-*"):
                if p.name.endswith(("-shm", "-wal")):
                    self.delete.append((p, "orphan -shm/-wal of a backup"))
            for p in sorted(D.glob(".fuse_hidden*")):
                self.delete.append((p, "file-sharing leftover (only if not open)"))
        gi = [l.strip() for l in (R / ".gitignore").read_text().splitlines()]
        if "archive/" not in gi or "/sample_mktdata.py" not in gi:
            sys.exit("REFUSED: .gitignore does not hold the lines expected (archive/, /sample_mktdata.py)")
        # names that must never move
        for src, _, _ in self.gitmv + self.mv:
            if src.parent == R and src.name in PRODUCTION:
                sys.exit(f"REFUSED: plan would move production file {src.name}")
        for _, dst, _ in self.gitmv + self.mv + self.dbmove:
            if dst.exists():
                sys.exit(f"REFUSED: destination exists: {dst}")
        return self

    def show(self):
        R = self.repo
        rel = lambda p: os.path.relpath(p, R) if str(p).startswith(str(R)) else str(p)
        def block(title, rows, fmt):
            print(f"\n== {title}: {len(rows)}")
            groups = {}
            for r in rows:
                groups.setdefault(r[-1], []).append(r)
            for why, rs in groups.items():
                print(f"  -- {why}: {len(rs)}")
                for r in rs:
                    print("     " + fmt(r))
        block("git mv", self.gitmv, lambda r: f"{rel(r[0])}  ->  {rel(r[1])}")
        block("move (untracked)", self.mv, lambda r: f"{rel(r[0])}  ->  {rel(r[1])}")
        block("database copies moved out of the repo", self.dbmove,
              lambda r: f"{rel(r[0])}  ->  {r[1]}  ({r[0].stat().st_size/1e6:.0f} MB)")
        block("delete", self.delete, lambda r: rel(r[0]))
        for n in self.notes:
            print("  note: " + n)

def edit_gitignore(repo):
    p = Path(repo) / ".gitignore"
    lines = p.read_text().splitlines()
    out, seen = [], set()
    for ln in lines:
        s = ln.strip()
        if s == "archive/":
            out.append("/archive/local/"); seen.add("archive"); continue
        if s == "/sample_mktdata.py":
            seen.add("sampler"); continue
        out.append(ln)
    if "archive" not in seen or "sampler" not in seen:
        sys.exit("REFUSED: .gitignore does not hold the lines expected (archive/, /sample_mktdata.py)")
    # `archive/` (unanchored) also hid data/archive/ -- keep that one hidden
    out += ["/data/archive/", "/_s122/px/", "/_s122/lc_backtest_results.json"]
    p.write_text("\n".join(out) + "\n")

def lsof_open(path):
    if not shutil.which("lsof"):
        return True                     # can't tell -> treat as open, keep it
    r = subprocess.run(["lsof", str(path)], capture_output=True, text=True)
    return r.returncode == 0 and r.stdout.strip() != ""

def execute(plan, log):
    R = plan.repo
    for src, dst, why in plan.gitmv:
        dst.parent.mkdir(parents=True, exist_ok=True)
        git(R, "mv", os.path.relpath(src, R), os.path.relpath(dst, R))
        log.append(f"git mv  {os.path.relpath(src, R)} -> {os.path.relpath(dst, R)}")
    edit_gitignore(R)
    log.append("edited  .gitignore")
    # untracked files that came along inside git-moved session folders
    for src, dst, _ in plan.gitmv:
        if dst.is_dir():
            for f in untracked_visible(R, os.path.relpath(dst, R)):
                fsrc = R / f
                fdst = R / "archive/local/sessions" / Path(f).relative_to("archive/sessions")
                fdst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(fsrc), str(fdst))
                log.append(f"move    {f} -> {os.path.relpath(fdst, R)}")
    for src, dst, why in plan.mv:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        log.append(f"move    {os.path.relpath(src, R)} -> {os.path.relpath(dst, R)}")
    for src, dst, why in plan.dbmove:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        log.append(f"move    {os.path.relpath(src, R)} -> {dst}")
    for p, why in plan.delete:
        if not p.exists():
            continue
        if p.name.startswith(".fuse_hidden") and lsof_open(p):
            log.append(f"kept    {p} (open, or lsof unavailable)"); continue
        p.unlink()
        log.append(f"delete  {p}")

def status_summary(repo):
    out = git(repo, "status", "--porcelain=v1", "-uall")
    codes = {}
    other = []
    for ln in out.splitlines():
        c = ln[:2]
        codes[c] = codes.get(c, 0) + 1
        if not c.startswith("R"):
            other.append(ln)
    print("\n== resulting git status: " +
          ", ".join(f"{k.strip() or k!r}: {v}" for k, v in sorted(codes.items())))
    print("  (R = renamed, staged by git mv; everything else listed:)")
    for ln in other:
        print("     " + ln)
    print("  " + git(repo, "status", "-sb").splitlines()[0])

def preflight(repo, force_window):
    now = dt.datetime.now()
    if now.weekday() < 5 and not force_window:
        hm = (now.hour, now.minute)
        for a, b in WINDOWS:
            if a <= hm <= b:
                sys.exit(f"REFUSED: inside a snapshot window ({a[0]:02}:{a[1]:02}-{b[0]:02}:{b[1]:02}).")
    if (Path(repo) / ".git/index.lock").exists():
        sys.exit("REFUSED: .git/index.lock exists -- rm -f .git/index.lock first, if no git is running.")
    dirty = [l for l in git(repo, "status", "--porcelain=v1").splitlines() if not l.startswith("??")]
    if dirty:
        sys.exit("REFUSED: staged or modified tracked files -- commit or set them aside first:\n" + "\n".join(dirty))

def main():
    here = Path(__file__).resolve().parents[1]
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=str(here))
    ap.add_argument("--pg", default=str(here.parent / "helm-pg"))
    ap.add_argument("--backups", default=str(Path.home() / "Backups/helm/pre-repair"))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--force-window", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args()
    repo = Path(a.repo).resolve()
    pg = Path(a.pg).resolve() if Path(a.pg).is_dir() else None
    preflight(repo, a.force_window)
    plan = Plan(repo, pg, a.backups).build()
    plan.show()
    if not a.apply:
        tmp = Path(tempfile.mkdtemp(prefix="helm-tidy-"))
        copy = tmp / "helm"
        shutil.copytree(repo, copy, symlinks=True,
                        ignore=lambda d, n: [x for x in n if ".db" in x or x.startswith(".fuse_hidden")]
                                            if Path(d) == repo / "data" else [])
        cplan = Plan(copy, None, tmp / "pre-repair").build()
        log = []
        execute(cplan, log)
        status_summary(copy)
        print(f"\nDRY RUN -- nothing changed. The plan was applied to a copy: {copy}")
        print("(helm-pg's .bak deletions and the database files are not in the copy; they are listed above.)")
        return
    if not a.yes and input("\nApply all of the above? [y/N] ").strip().lower() != "y":
        sys.exit("Nothing changed.")
    log = [f"# Folder tidy, operations review step 1 -- {dt.datetime.now():%Y-%m-%d %H:%M} (s141)", ""]
    try:
        execute(plan, log)
    finally:
        (repo / "archive").mkdir(exist_ok=True)
        (repo / "archive/TIDY-2026-10-31-s141.md").write_text("\n".join(log) + "\n")
    status_summary(repo)
    print(f"\nDone. Log: archive/TIDY-2026-10-31-s141.md ({len(log)-2} actions)")

if __name__ == "__main__":
    main()
