"""s166 rough replay (read-only): the account's 'any two of three' close line on HELM's REAL sold puts.
Fires at the first stored reading where two hold: stock below strike; buy-back (stored mid) >= 2x premium;
stock below break-even (strike - premium). Exit priced at that reading's mid. Compared with what was booked.
Usage: python3 two_of_three.py DB"""
import sqlite3, sys
db = sqlite3.connect("file:%s?mode=ro" % sys.argv[1], uri=True); db.row_factory = sqlite3.Row
rows = db.execute("SELECT p.id,p.ticker,p.opened_at,p.closed_at,p.exit_reason,l.strike,l.open_price,l.close_price,l.contracts,"
                  "l.multiplier,l.id lid FROM positions p JOIN legs l ON l.position_id=p.id WHERE p.book='REAL' AND p.strategy='CSP' "
                  "AND p.status='CLOSED' AND l.leg_role='SHORT_PUT' AND l.close_price IS NOT NULL").fetchall()
per = lambda d: "1 before 08-17" if d < "2026-08-17" else ("2 08-17..09-25" if d < "2026-09-26" else ("3 09-26..10-23" if d < "2026-10-24" else "4 since 10-24"))
out = {}; detail = []
for r in rows:
    if db.execute("SELECT count(*) FROM legs WHERE position_id=?", (r["id"],)).fetchone()[0] != 1: continue
    cks = db.execute("SELECT checked_at,spot_price,current_price,data_quality FROM checks WHERE position_id=? AND checked_at<=? "
                     "AND spot_price IS NOT NULL AND current_price IS NOT NULL ORDER BY checked_at", (r["id"], r["closed_at"])).fetchall()
    if not cks: continue
    k, prem, n, m = r["strike"], r["open_price"], r["contracts"], r["multiplier"] or 100
    actual = (prem - r["close_price"]) * n * m
    fired = None
    for c in cks:
        if (c["data_quality"] or "GOOD") != "GOOD": continue
        a, b, e = c["spot_price"] < k, c["current_price"] >= 2 * prem, c["spot_price"] < k - prem
        if a + b + e >= 2:
            fired = (c, a, b, e); break
    rule = actual if fired is None else (prem - fired[0]["current_price"]) * n * m
    g = out.setdefault(per(r["opened_at"][:10]), dict(n=0, fired=0, actual=0.0, rule=0.0, better=0, worse=0, be_only=0))
    g["n"] += 1; g["actual"] += actual; g["rule"] += rule
    if fired:
        g["fired"] += 1; g["better"] += rule > actual + 1; g["worse"] += rule < actual - 1
        g["be_only"] += (fired[3] and not fired[2])
        detail.append((r["opened_at"][:10], r["ticker"], n, k, prem, fired[0]["checked_at"][:16], round(fired[0]["spot_price"], 2),
                       fired[0]["current_price"], "strike" * fired[1] + " 2x" * fired[2] + " b/e" * fired[3], round(rule), round(actual), r["exit_reason"]))
for kk in sorted(out):
    g = out[kk]; print("%-16s puts %3d  rule fired %2d (better %d, worse %d; break-even without 2x: %d)  booked $%9.0f  with rule $%9.0f  diff $%8.0f"
                       % (kk, g["n"], g["fired"], g["better"], g["worse"], g["be_only"], g["actual"], g["rule"], g["rule"] - g["actual"]))
for d in sorted(detail): print(d)
