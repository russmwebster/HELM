"""s115 commits, run on the Mac (the VM has no git identity; the bridge
whitelists python3 but not git itself). Named files only. No push here."""
import subprocess, sys
def sh(cwd, *args):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr).strip()
TRAIL = ("\n\nCo-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>\n"
         "Claude-Session: https://claude.ai/code/session_01KiCUsJGv8FbWndX2DLG5WS")
H = "/Users/russmacbookpro/Projects/helm"; PG = "/Users/russmacbookpro/Projects/helm-pg"
plan = [
  (H, ["helm/exit_flags.py", "helm/rule_read.py", "helm/cli/pending_cmd.py", "helm.py",
       "helm/cli/check_cmd.py", "tools/exit_discipline.py", "_s115"],
   "W158 + W166: exit-flag decision log (helm pending) and the paper-rule read; M6 has a source\n\n"
   "W158: helm/exit_flags.py + helm/cli/pending_cmd.py. One new table (exit_flags), one open\n"
   "decision per (position, kind); ACTED inferred from the close, KEEP logged by Russ. Hooked\n"
   "after the snapshot in check_cmd; installed live with 11 seeded rows. tools/exit_discipline.py\n"
   "M6 now reads the log; denominator unchanged, --selftest PASS on all 173 keys.\n"
   "W166: helm/rule_read.py renders what the paper doctrine would do to a REAL position, from\n"
   "check_one's core_reason+arms on the board and the latest journaled check on the card.\n"
   "Mirror verified against 145 paper closes (138 agree, 7 mark-timing). Informs, never acts."),
  (PG, ["helm_engine.py", "engine_store.py", "templates/positions.html", "templates/thesis.html"],
   "W166: the paper rule's verdict on the real book - row marker and card explanation\n\n"
   "_VERDICT_MAP speaks the v3 long-side reasons; rows carry rule=from_assessment(a,pos);\n"
   "thesis_card attaches rule=from_journal(latest check). Display only (HELM-193)."),
]
for cwd, files, msg in plan:
    rc, out = sh(cwd, "add", "--", *files)
    if rc: print("ADD FAILED", cwd, out); sys.exit(1)
    rc, out = sh(cwd, "commit", "-m", msg + TRAIL)
    print(cwd.split("/")[-1], "commit rc", rc); print(out.splitlines()[0] if out else "")
    rc, out = sh(cwd, "log", "--oneline", "-1"); print("  ", out)
    rc, out = sh(cwd, "status", "--porcelain"); print("   still modified/untracked:", [l for l in out.splitlines() if not l.startswith("?? ") or "_s1" in l][:6])
    rc, out = sh(cwd, "rev-list", "--left-right", "--count", "@{u}...HEAD"); print("   behind/ahead:", out)
