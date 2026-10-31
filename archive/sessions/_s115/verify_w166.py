"""W166: does rule_read.from_journal agree with the engine that actually
acted? Every PAPER close in the window whose exit_reason is an acting reason
is replayed from the last GOOD check on or before the close. Read-only."""
import os, sys, sqlite3
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("HELM_ROOT", ROOT)
from helm import rule_read as R
db = os.environ.get("HELM_DB") or os.path.join(ROOT, "data", "helm.db")
c = sqlite3.connect("file:%s?mode=ro" % db, uri=True); c.row_factory = sqlite3.Row
rows = [dict(r) for r in c.execute("""select * from positions where book='PAPER' and status='CLOSED'
    and closed_at >= '2026-07-27' and exit_reason in ('PROFIT_TARGET','DTE_MANAGE','EXPIRY','GIVE_BACK','STOP_LOSS','DTE_21','DTE_7')
    order by closed_at""")]
agree = disagree = skipped = 0; miss = []
for p in rows:
    legs = [dict(r) for r in c.execute("select * from legs where position_id=?", (p["id"],))]
    ch = c.execute("""select * from checks where position_id=? and data_quality='GOOD' and checked_at<=?
                      order by checked_at desc limit 1""", (p["id"], p["closed_at"])).fetchone()
    if not ch: skipped += 1; continue
    v = R.from_journal(p, legs, dict(ch))
    if v is None: skipped += 1; continue
    got = v["reason"]; want = p["exit_reason"]
    # STOP_LOSS/GIVE_BACK etc. are exact; PROFIT_TARGET vs DTE_MANAGE precedence is exact too
    if got == want: agree += 1
    else:
        disagree += 1; miss.append((p["ticker"], p["strategy"], p["closed_at"][:10], want, got, ch["checked_at"][:16], ch["pnl_pct"], ch["dte_now"]))
print("PAPER acting closes since 07-27: %d | agree %d | disagree %d | no journal %d" % (len(rows), agree, disagree, skipped))
for m in miss: print("  MISS %-5s %-16s closed %s  agent=%-13s mirror=%-13s  last check %s pnl %s dte %s" % m)
print("\n-- the open REAL book, from the journal --")
for p in c.execute("select * from positions where book='REAL' and status='OPEN' order by strategy,ticker"):
    p = dict(p)
    legs = [dict(r) for r in c.execute("select * from legs where position_id=?", (p["id"],))]
    ch = c.execute("select * from checks where position_id=? and data_quality='GOOD' order by checked_at desc limit 1", (p["id"],)).fetchone()
    v = R.from_journal(p, legs, dict(ch) if ch else None)
    print("  %-5s %-12s %s" % (p["ticker"], p["strategy"], (("FIRES " + v["reason"]) if v and v["fires"] else ("hold" if v else "no read")) ))
sys.exit(1 if disagree > max(1, len(rows) // 10) else 0)
