"""tools/verify_w180_step5.py -- W180 step 5: the diagonal's exit flags.

    python3 tools/verify_w180_step5.py [--module PATH] [--db LIVE_DB]

Loads exit_flags from PATH (default: the repo's helm/exit_flags.py) and SAYS
WHICH FILE IT LOADED. Every case builds its own fixture in a scratch SQLite
database; nothing depends on live state. With --db, one extra case runs the
module against a VACUUM INTO copy of that database and checks the two facts
measured on 2026-09-23 (UNH's 09-18 dte flag settles as HARVEST; a second scan
writes nothing).

The feared defects, each with a case that must fail if it comes back:
  * rent leaking into the long-only percentage (Russ, 09-23: the long's OWN
    move over its original debit)                          -> case denominator
  * the primary leg's mark missing from leg_checks and read as "no short"
    (AA/EQT/UNH, measured 09-23)                           -> case primary_mark
  * a standing condition re-opening a decision every day   -> case episodic
"""
import importlib.util, os, sqlite3, sys, tempfile, hashlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("HELM_ROOT") or os.path.dirname(HERE)
sys.path.insert(0, ROOT)

def _arg(n, d=None):
    return sys.argv[sys.argv.index(n) + 1] if n in sys.argv else d

MOD = os.path.abspath(_arg("--module", os.path.join(ROOT, "helm", "exit_flags.py")))
spec = importlib.util.spec_from_file_location("helm.exit_flags", MOD)
EF = importlib.util.module_from_spec(spec)
sys.modules["helm.exit_flags"] = EF
spec.loader.exec_module(EF)
print("LOADED %s  sha256 %s" % (MOD, hashlib.sha256(open(MOD, "rb").read()).hexdigest()[:12]))

FAILS = []
def case(name, got, want):
    ok = got == want
    print("%s  %-16s got %r%s" % ("PASS" if ok else "FAIL", name, got,
                                  "" if ok else "   want %r" % (want,)))
    if not ok:
        FAILS.append(name)

def leg(i, d, k, exp, op, status="OPEN", cd=None, od="2026-09-01"):
    return {"id": i, "leg_role": ("SHORT_CALL" if d == "SHORT" else "LONG_CALL"),
            "option_type": "CALL", "direction": d, "strike": k, "expiration": exp,
            "contracts": 1, "multiplier": 100, "open_price": op, "close_price": None,
            "open_date": od, "close_date": cd, "status": status, "created_at": od}

def row(t, spot, marks, pct=0.0):
    return {"checked_at": t, "spot_price": spot, "pnl_pct": pct, "pnl_unrealized": 0.0,
            "marks": marks}

def last_kinds(legs, rows):
    return EF.diag_series(legs, rows)[-1][1]

S = leg("S", "SHORT", 50.0, "2026-10-16", 2.00)
L = leg("L", "LONG", 45.0, "2026-12-18", 8.00)

# --- SHORT ON ---------------------------------------------------------------
case("harvest25", last_kinds([S, L], [row("2026-09-10T10:00", 45, {"S": 1.40, "L": 8})]),
     ["harvest25"])
case("harvest50", last_kinds([S, L], [row("2026-09-10T10:00", 45, {"S": 0.80, "L": 8})]),
     ["harvest50", "harvest25"])
case("worthless", last_kinds([S, L], [row("2026-09-10T10:00", 40, {"S": 0.05, "L": 5})]),
     ["worthless", "harvest50", "harvest25"])
case("nothing", last_kinds([S, L], [row("2026-09-10T10:00", 45, {"S": 1.90, "L": 8})]), [])
one = [row("2026-09-10T10:00", 51, {"S": 3.0, "L": 9})]
case("breach_1day", last_kinds([S, L], one), [])
two = one + [row("2026-09-11T10:00", 51.5, {"S": 3.2, "L": 9.5})]
case("breach_2days", last_kinds([S, L], two), ["breach"])
gap = one + [row("2026-09-11T10:00", 49, {"S": 2.0, "L": 8}),
             row("2026-09-12T10:00", 51, {"S": 3.0, "L": 9})]
case("breach_broken", last_kinds([S, L], gap), [])
case("pos_stop", last_kinds([S, L], [row("2026-09-10T10:00", 45, {"S": 1.9, "L": 5}, pct=-55)]),
     ["pos_stop"])

# --- LONG ONLY --------------------------------------------------------------
Sc = leg("S", "SHORT", 50.0, "2026-10-16", 2.00, status="CLOSED", cd="2026-09-12T11:00")
Sc["close_price"] = 0.20
case("bare", last_kinds([Sc, L], [row("2026-09-15T10:00", 45, {"L": 8.2})]), ["bare"])
case("long_stop", last_kinds([Sc, L], [row("2026-09-15T10:00", 40, {"L": 3.9})]),
     ["long_stop", "bare"])
# denominator: long own move -51%; with the $1.80 rent it would read -29%.
case("denominator", last_kinds([Sc, L], [row("2026-09-15T10:00", 40, {"L": 3.92})]),
     ["long_stop", "bare"])
gb = [row("2026-09-05T10:00", 48, {"S": 2.5, "L": 10.4}),     # long peak +30%
      row("2026-09-15T10:00", 46, {"L": 8.4})]                  # +5%: 25 pts back
case("giveback", last_kinds([Sc, L], gb), ["long_giveback", "bare"])
Ln = leg("L", "LONG", 45.0, "2026-10-05", 8.00)
case("long_dte21", last_kinds([Sc, Ln], [row("2026-09-15T10:00", 44, {"L": 7.8})]),
     ["long_dte21", "bare"])
case("long_dte21_pos", last_kinds([Sc, Ln], [row("2026-09-15T10:00", 47, {"L": 8.3})]),
     ["bare"])
case("long_dte7", last_kinds([Sc, Ln], [row("2026-09-29T10:00", 47, {"L": 8.3})]),
     ["long_dte7", "bare"])
# a short closed AFTER the check is still live at the check
case("live_at", last_kinds([Sc, L], [row("2026-09-12T10:00", 45, {"S": 0.3, "L": 8})]),
     ["harvest50", "harvest25"])

# --- episodic re-fire ---------------------------------------------------------
rs = [row("2026-09-15T10:00", 45, {"L": 8.2}), row("2026-09-16T10:00", 45, {"L": 8.2})]
got = [k for k, _r, _i in EF.diag_new_flags([Sc, L], rs, set(), {"bare": "2026-09-15"})]
case("episodic_same", got, [])
# same condition, a new episode: re-sold (SHORT ON on the 17th), bare again the 18th
S2 = leg("S2", "SHORT", 52.0, "2026-10-23", 1.5, od="2026-09-17", status="CLOSED",
         cd="2026-09-17T15:00")
rs2 = rs + [row("2026-09-17T10:00", 45, {"S2": 1.4, "L": 8.2}),
            row("2026-09-18T10:00", 45, {"L": 8.2})]
got = [k for k, _r, _i in EF.diag_new_flags([Sc, S2, L], rs2, set(), {"bare": "2026-09-15"})]
case("episodic_new", got, ["bare"])
got = [k for k, _r, _i in EF.diag_new_flags([S, L], [row("2026-09-10T10:00", 45, {"S": 0.8, "L": 8})],
                                           {"harvest50"}, {})]
case("already_open", got, ["harvest25"])

# --- database cases -----------------------------------------------------------
def fixture():
    fd, p = tempfile.mkstemp(suffix=".db"); os.close(fd)
    c = sqlite3.connect(p)
    c.executescript("""
    CREATE TABLE positions (id TEXT, ticker TEXT, strategy TEXT, status TEXT, book TEXT,
        closed_at TEXT, exit_reason TEXT);
    CREATE TABLE legs (id TEXT, position_id TEXT, leg_role TEXT, option_type TEXT,
        direction TEXT, strike REAL, expiration TEXT, contracts INT, multiplier INT,
        open_price REAL, close_price REAL, open_date TEXT, close_date TEXT, status TEXT,
        notes TEXT, created_at TEXT, entry_delta REAL);
    CREATE TABLE checks (id INTEGER PRIMARY KEY, position_id TEXT, checked_at TEXT,
        spot_price REAL, dte_now INT, pnl_pct REAL, pnl_unrealized REAL,
        current_price REAL, thesis_broken INT, data_quality TEXT);
    CREATE TABLE leg_checks (id INTEGER PRIMARY KEY, check_id INT, position_id TEXT,
        leg_id TEXT, checked_at TEXT, current_price REAL);
    CREATE TABLE lifecycle_events (id INTEGER PRIMARY KEY, position_id TEXT, leg_id TEXT,
        occurred_at TEXT, narrative TEXT);
    """)
    c.execute("INSERT INTO positions VALUES ('P','XX','DIAGONAL','OPEN','REAL',NULL,NULL)")
    return c, p

# primary_mark: the short's mark lives on checks.current_price only (AA/EQT/UNH).
c, p = fixture()
c.execute("INSERT INTO legs VALUES ('P-SH','P','SHORT_CALL','CALL','SHORT',50,'2026-10-16',1,100,2.0,NULL,'2026-09-01',NULL,'OPEN',NULL,'2026-09-01',NULL)")
c.execute("INSERT INTO legs VALUES ('P-LO','P','LONG_CALL','CALL','LONG',45,'2026-12-18',1,100,8.0,NULL,'2026-09-01',NULL,'OPEN',NULL,'2026-09-01',NULL)")
c.execute("INSERT INTO checks VALUES (1,'P','2026-09-10T10:00',45,36,-5,-40,0.80,0,'GOOD')")
c.execute("INSERT INTO leg_checks VALUES (1,1,'P','P-LO','2026-09-10T10:00',7.6)")
c.commit()
case("primary_mark", EF.diag_firing_now(c, "P"), {"harvest50", "harvest25"})
w = EF.scan(c)
case("scan_writes", sorted(x["kind"] for x in w), ["harvest25", "harvest50"])
case("scan_again", len(EF.scan(c)), 0)
# the harvest: close the short (as close_leg writes it), then settle.
c.execute("UPDATE legs SET status='CLOSED', close_price=0.8, close_date='2026-09-10T11:00' WHERE id='P-SH'")
c.execute("INSERT INTO lifecycle_events VALUES (1,'P','P-SH','2026-09-10T11:00','LEG_CLOSED | reason=HARVEST | SHORT_CALL SHORT $50 | W180')")
c.commit()
case("settle_legs", EF.settle_legs(c), 2)
# found live 09-23: a scan AFTER the harvest re-emits from the day's earlier
# rows; those flags must come out already settled, not open.
c.execute("DELETE FROM exit_flags")
c.commit()
EF.scan(c)
case("born_settled", c.execute("SELECT COUNT(*) FROM exit_flags WHERE disposition IS NULL "
     "AND kind IN ('harvest50','harvest25')").fetchone()[0], 0)
c.execute("DELETE FROM exit_flags")
c.commit()
EF.scan(c); EF.settle_legs(c)
case("settled_as", sorted(c.execute("SELECT kind, disposition, decided_by, reason FROM exit_flags").fetchall()),
     [("harvest25", "ACTED", "inferred-leg-close", "HARVEST"),
      ("harvest50", "ACTED", "inferred-leg-close", "HARVEST")])
# next day, LONG ONLY: bare fires; primary is now the long, on current_price
c.execute("INSERT INTO checks VALUES (2,'P','2026-09-11T10:00',44,98,-10,-80,7.2,0,'GOOD')")
c.commit()
case("bare_fires", sorted(x["kind"] for x in EF.scan(c)), ["bare"])
# keep() must not erase the reading the flag fired on
EF.keep(c, "P", "bare", "waiting for a bounce")
n = c.execute("SELECT note FROM exit_flags WHERE kind='bare'").fetchone()[0]
case("keep_note", (n or "").startswith("long -10% of its 8.00 debit"), True)
# a re-sell answers `bare` (a fresh open decision, so pretend it wasn't kept)
c.execute("UPDATE exit_flags SET disposition=NULL WHERE kind='bare'")
c.execute("INSERT INTO legs VALUES ('P-SH2','P','SHORT_CALL','CALL','SHORT',52,'2026-10-23',1,100,1.5,NULL,'2026-09-12',NULL,'OPEN',NULL,'2026-09-12',NULL)")
c.commit()
EF.settle_legs(c)
case("resell_settles", c.execute("SELECT disposition, decided_by FROM exit_flags WHERE kind='bare'").fetchone(),
     ("ACTED", "inferred-resell"))
c.close(); os.unlink(p)

# non-diagonal: classify is untouched
case("csp_target", EF.classify("CSP", 55, 30, 0), "target")
case("lc_thesis", EF.classify("LONG_CALL", 55, 10, 1), "thesis")

# --- live copy ------------------------------------------------------------------
live = _arg("--db")
if live:
    fd, cp = tempfile.mkstemp(suffix=".db"); os.close(fd); os.unlink(cp)
    s = sqlite3.connect("file:%s?mode=ro" % live, uri=True)
    s.execute("VACUUM INTO ?", (cp,)); s.close()
    c = sqlite3.connect(cp)
    EF.settle_closed(c)
    r = c.execute("SELECT f.disposition, f.reason FROM exit_flags f JOIN positions p "
                  "ON p.id=f.position_id WHERE p.ticker='UNH' AND f.kind='dte' "
                  "AND f.flag_date='2026-09-18'").fetchone()
    case("copy_unh_dte", r, ("ACTED", "HARVEST"))
    EF.scan(c)
    case("copy_rescan", len(EF.scan(c)), 0)
    c.close(); os.unlink(cp)

print("\n%d FAIL%s" % (len(FAILS), "" if len(FAILS) == 1 else "S") if FAILS else "\nALL PASS")
sys.exit(1 if FAILS else 0)
