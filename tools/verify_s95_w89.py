#!/usr/bin/env python3
"""W89 behavioral checks — nearest-wall intrinsic buffer for multi-short structures.

Run BEFORE the patch: the condor/strangle checks must FAIL (buffer None).
Run AFTER the patch: all checks must PASS.

Read-only. Touches no database in write mode; the real-condor cross-check
opens the s95 snapshot read-only.
"""
import os, sys, sqlite3, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from helm.cli.check_cmd import assess_position          # noqa: E402
from helm.verdict import band_for                        # noqa: E402

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print("  ok   %s" % name)
    else:
        FAIL += 1; print("  FAIL %s  %s" % (name, detail))

def leg(direction, opt_type, strike):
    return {"direction": direction, "option_type": opt_type, "strike": strike,
            "expiration": "2026-09-18", "contracts": 1, "multiplier": 100,
            "open_price": 1.0, "leg_role": "%s_%s" % (direction, opt_type),
            "status": "OPEN", "id": "L-%s-%s-%s" % (direction, opt_type, strike)}

def assess(legs, spot, strategy="IRON_CONDOR"):
    pos = {"strategy": strategy, "net_premium": 500.0}
    try:
        return assess_position(pos, legs, spot, {}, {})
    except Exception:
        traceback.print_exc()
        return None

print("== W89 checks ==")

# 1. condor, put wall nearer
a = assess([leg("SHORT","PUT",100), leg("LONG","PUT",95),
            leg("SHORT","CALL",120), leg("LONG","CALL",125)], 105.0)
check("condor put-wall nearer: buffer 5.00",
      a is not None and a.get("intrinsic_buffer") == 5.0, repr(a and a.get("intrinsic_buffer")))
check("condor put-wall nearer: strike 100 / side put",
      a is not None and a.get("buffer_strike") == 100 and a.get("buffer_side") == "put",
      repr((a and a.get("buffer_strike"), a and a.get("buffer_side"))))

# 2. condor, call wall nearer
a = assess([leg("SHORT","PUT",100), leg("LONG","PUT",95),
            leg("SHORT","CALL",120), leg("LONG","CALL",125)], 118.0)
check("condor call-wall nearer: buffer 2.00 / side call",
      a is not None and a.get("intrinsic_buffer") == 2.0 and a.get("buffer_side") == "call",
      repr(a and (a.get("intrinsic_buffer"), a.get("buffer_side"))))

# 3. condor, put wall breached -> negative
a = assess([leg("SHORT","PUT",100), leg("LONG","PUT",95),
            leg("SHORT","CALL",120), leg("LONG","CALL",125)], 98.0)
check("condor breached: buffer -2.00",
      a is not None and a.get("intrinsic_buffer") == -2.0, repr(a and a.get("intrinsic_buffer")))

# 4. CSP single short put — value identical to the old formula
a = assess([leg("SHORT","PUT",100)], 105.0, "CSP")
check("CSP unchanged: buffer 5.00", a is not None and a.get("intrinsic_buffer") == 5.0,
      repr(a and a.get("intrinsic_buffer")))
check("CSP carries strike/side", a is not None and a.get("buffer_strike") == 100
      and a.get("buffer_side") == "put", repr(a and (a.get("buffer_strike"), a.get("buffer_side"))))

# 5. single short call
a = assess([leg("SHORT","CALL",120)], 105.0, "COVERED_CALL")
check("short call unchanged: buffer 15.00",
      a is not None and a.get("intrinsic_buffer") == 15.0, repr(a and a.get("intrinsic_buffer")))

# 6. long-only: stays None
a = assess([leg("LONG","CALL",110)], 105.0, "LONG_CALL")
check("long debit: buffer None", a is not None and a.get("intrinsic_buffer") is None,
      repr(a and a.get("intrinsic_buffer")))

# 7. fail closed: one short leg missing its strike -> None, never a half answer
a = assess([leg("SHORT","PUT",100), leg("SHORT","CALL",None)], 105.0, "SHORT_STRANGLE")
check("missing wall fails closed: buffer None",
      a is not None and a.get("intrinsic_buffer") is None, repr(a and a.get("intrinsic_buffer")))

# 8. strangle
a = assess([leg("SHORT","PUT",100), leg("SHORT","CALL",108)], 105.0, "SHORT_STRANGLE")
check("strangle call-wall nearer: buffer 3.00 / side call",
      a is not None and a.get("intrinsic_buffer") == 3.0 and a.get("buffer_side") == "call",
      repr(a and (a.get("intrinsic_buffer"), a.get("buffer_side"))))

# 9. VERDICT PARITY — the multileg band must be identical whether or not
#    intrinsic_buffer is filled in (band_for reads it only on the single-leg branch).
for reason in (None,):
    ev_none = {"pnl_pct": -20.0, "intrinsic_buffer": None, "pct_buffer": None,
               "mark_confidence": "live", "direction": "SHORT", "is_multileg": True,
               "proximity_pct": 0.55, "tested_side": "call"}
    ev_full = dict(ev_none, intrinsic_buffer=2.0, pct_buffer=1.9)
    b1, b2 = band_for(reason, ev_none), band_for(reason, ev_full)
    check("band_for multileg parity (flag)", b1["flag"] == b2["flag"], "%s vs %s" % (b1["flag"], b2["flag"]))
    check("band_for multileg parity (headline)", b1["headline"] == b2["headline"],
          "%r vs %r" % (b1["headline"], b2["headline"]))

# 10. single-short parity across a spot grid — new code must equal old formula
ok = True
for spot in (80.0, 95.0, 99.99, 100.0, 100.01, 113.7, 250.0):
    a = assess([leg("SHORT","PUT",100), leg("LONG","PUT",95)], spot, "BULL_PUT_SPREAD")
    want = round(spot - 100, 2)
    if a is None or a.get("intrinsic_buffer") != want:
        ok = False; print("    grid mismatch at spot", spot, a and a.get("intrinsic_buffer"), "want", want)
check("single-short parity across grid", ok)

# 11. real condor from the s95 snapshot — assessment agrees with SQL nearest-wall
SNAP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "data", "helm-snap-s95-w88.db")
if os.path.exists(SNAP):
    c = sqlite3.connect("file:%s?mode=ro" % SNAP, uri=True); c.row_factory = sqlite3.Row
    row = c.execute("""select p.id, k.spot_price from positions p
                       join checks k on k.position_id=p.id
                       where p.strategy='IRON_CONDOR' and p.status='OPEN'
                         and k.data_quality='GOOD' and k.spot_price is not null
                       order by k.checked_at desc limit 1""").fetchone()
    if row:
        legs_db = [dict(r) for r in c.execute("select * from legs where position_id=?", (row["id"],))]
        pos_db = dict(c.execute("select * from positions where id=?", (row["id"],)).fetchone())
        spot = row["spot_price"]
        exp = None
        for l in legs_db:
            if l["direction"] == "SHORT" and l["strike"] is not None and l["option_type"]:
                d = (spot - l["strike"]) if l["option_type"] == "PUT" else (l["strike"] - spot)
                exp = d if exp is None else min(exp, d)
        try:
            a = assess_position(pos_db, legs_db, spot, {}, {})
            got = a.get("intrinsic_buffer")
            check("real condor %s @ %.2f: buffer %.2f" % (row["id"], spot, exp or -1),
                  got is not None and exp is not None and abs(got - round(exp, 2)) < 0.011,
                  "got %r want %r" % (got, exp))
        except Exception:
            traceback.print_exc(); check("real condor assessment ran", False)
    else:
        print("  (no open condor in snapshot — cross-check skipped)")
else:
    print("  (snapshot missing — cross-check skipped)")

print("== %d passed, %d failed ==" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
