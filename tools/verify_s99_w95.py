# -*- coding: utf-8 -*-
"""HELM-151 (W95) -- the card must not contradict itself about what acts.

Written to RUN (and fail honestly) against pre-change code: every new
constant/function is resolved with getattr, so the control run produces
behavioural failures rather than aborting on an AttributeError at import.

Read-only: the live DB is opened mode=ro and never written.
"""
import os, re, sqlite3, sys, traceback

ROOT = os.environ.get("HELM_ROOT", "/Users/russmacbookpro/Projects/helm")
sys.path.insert(0, ROOT)
DB = os.path.join(ROOT, "data", "helm.db")

PASS, FAIL = [], []
def check(name, fn):
    try:
        ok, detail = fn()
    except Exception as e:
        FAIL.append((name, "EXC %s: %s" % (type(e).__name__, e)))
        return
    (PASS if ok else FAIL).append((name, detail))

from helm import long_exit as le
from helm import thesis as th

V3 = ("STOP_LOSS", "GIVE_BACK", "DTE_7", "DTE_21")
BAD_PATTERNS = [
    r"this is the acting exit",
    r"ACTS \(THESIS_BREAK\)",
    r"already a gate",
    r"whose THESIS_BREAK exit was already",
]
REGISTER_REF = re.compile(r"W[0-9]{1,3}\b|HELM-[0-9]+|s9[0-9]")

def walk_strings(obj, path="card"):
    """Every string anywhere in the card. HELM-149's discipline: grepping one
    rendered page misses branches that only render in rare states."""
    if isinstance(obj, str):
        yield path, obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            for r in walk_strings(v, "%s.%s" % (path, k)):
                yield r
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            for r in walk_strings(v, "%s[%d]" % (path, i)):
                yield r

# ---- 1..3  the engine declares what acts, once ------------------------------
def t_acting_exists():
    av = getattr(le, "ACTING_VERDICTS", None)
    return av is not None, "ACTING_VERDICTS=%r" % (av,)
check("engine declares ACTING_VERDICTS", t_acting_exists)

def t_acting_value():
    av = tuple(getattr(le, "ACTING_VERDICTS", ()) or ())
    return av == V3, "%r" % (av,)
check("acting set is exactly the v3 four, in precedence order", t_acting_value)

def t_direction_derived():
    da = getattr(le, "DIRECTION_ACTS", None)
    av = tuple(getattr(le, "ACTING_VERDICTS", ()) or ())
    return da is False and ("THESIS_BREAK" not in av), "DIRECTION_ACTS=%r" % (da,)
check("DIRECTION_ACTS is False and consistent with the acting set", t_direction_derived)

# ---- 4  the rule really cannot emit THESIS_BREAK ----------------------------
def t_verdict_never_thesis_break():
    seen, entry, cur = set(), {"bias_score": 3.0, "source": "signals"}, {"bias_score": -3.0, "source": "live"}
    for pnl in (-900.0, -400.0, -1.0, 0.0, 50.0, 400.0):
        for dte in (3, 9, 15, 25, 60):
            for js in ({}, {"break_days": 5, "hwm_pct": 0.60}, {"break_days": 1, "hwm_pct": None}):
                r, _a = le.long_verdict(pnl, 1000.0, dte, entry, cur, js)
                seen.add(r)
    bad = seen & set(getattr(le, "RETIRED_VERDICTS", ("THESIS_BREAK",)))
    return (not bad) and seen <= (set(V3) | {None}), "emitted=%r" % (sorted(x for x in seen if x),)
check("long_verdict emits only the acting four, never a retired name", t_verdict_never_thesis_break)

# ---- 5  panel keys and card labels agree ------------------------------------
def t_labels_cover_acting():
    fn = getattr(th, "acting_rules", None)
    if fn is None:
        return False, "thesis.acting_rules missing"
    got = [n for n, _l in fn()]
    return tuple(got) == V3, "%r" % (got,)
check("card has a label for every acting verdict", t_labels_cover_acting)

def t_panel_keys_match():
    con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    con.row_factory = sqlite3.Row
    row = con.execute("select * from positions where strategy='LONG_CALL' "
                      "and closed_at is null limit 1").fetchone()
    if row is None:
        return False, "no open long to test"
    pos = dict(row); pos["status"] = "OPEN"
    checks = [dict(r) for r in con.execute(
        "select * from checks where position_id=? and data_quality='GOOD' "
        "order by checked_at", (pos["id"],))]
    et = le.entry_thesis(con, pos["id"])
    panel = th.exit_rules(pos, checks, checks[-1] if checks else None, et)
    if not panel:
        return False, "no panel rendered"
    keys = {r["key"] for r in panel["rows"]}
    want = {v.lower() for v in tuple(getattr(le, "ACTING_VERDICTS", ()) or ())}
    return keys == want and bool(want), "panel=%r want=%r" % (sorted(keys), sorted(want))
check("panel rows are exactly the acting verdicts (no rule shown that cannot act)", t_panel_keys_match)

# ---- 6..8  the three sentences that were wrong ------------------------------
def t_fine_print():
    fn = getattr(th, "_direction_fine", None)
    if fn is None:
        return False, "thesis._direction_fine missing"
    s = fn()
    bad = [p for p in BAD_PATTERNS if re.search(p, s)]
    return (not bad) and "closes nothing" in s, "%r" % (s[:110],)
check("direction fine print no longer claims the belief acts", t_fine_print)

def t_broken_branch():
    """The branch s98's read never saw: it only renders when a belief is BROKEN."""
    fn = getattr(th, "_direction_verdict_phrase", None)
    if fn is None:
        return False, "thesis._direction_verdict_phrase missing"
    s = fn()
    return "acting exit" not in s, "%r" % (s[:110],)
check("broken-state line no longer says 'this is the acting exit'", t_broken_branch)

def t_doctrine_long():
    fn = getattr(th, "doctrine_note", None)
    if fn is None:
        return False, "thesis.doctrine_note missing"
    s = fn("LONG_CALL", False)
    bad = [p for p in BAD_PATTERNS if re.search(p, s)]
    return (not bad) and "the stop" in s and "giving back the gain" in s, "%r" % (s[:170],)
check("doctrine footer names the acting rules and drops the THESIS_BREAK exception", t_doctrine_long)

def t_doctrine_closed():
    fn = getattr(th, "doctrine_note", None)
    if fn is None:
        return False, "thesis.doctrine_note missing"
    s = fn("LONG_CALL", True)
    return "nothing on it acts" in s and not any(re.search(p, s) for p in BAD_PATTERNS), "%r" % (s[:140],)
check("closed cards say nothing acts (the panel is absent there, so the footer is all there is)", t_doctrine_closed)

def t_doctrine_credit():
    fn = getattr(th, "doctrine_note", None)
    if fn is None:
        return False, "thesis.doctrine_note missing"
    s = fn("CSP", False)
    return "nothing on this card acts" in s and "the stop" not in s, "%r" % (s[:140],)
check("a credit card's footer does not advertise the long rules", t_doctrine_credit)

# ---- 9  it is DERIVED, not reworded ----------------------------------------
def t_actually_derived():
    """Flip the engine's acting set and the copy must flip with it. This is the
    assertion that stops v4 reintroducing W95: reworded prose passes every test
    above, but only derived prose passes this one."""
    fine = getattr(th, "_direction_fine", None)
    phrase = getattr(th, "_direction_verdict_phrase", None)
    if fine is None or phrase is None:
        return False, "helpers missing"
    orig = getattr(le, "ACTING_VERDICTS", ())
    orig_d = getattr(le, "DIRECTION_ACTS", False)
    try:
        le.ACTING_VERDICTS = ("THESIS_BREAK",) + tuple(orig)
        le.DIRECTION_ACTS = True
        flipped = (("ACTS (THESIS_BREAK)" in fine()) and
                   ("this is the acting exit" == phrase()))
    finally:
        le.ACTING_VERDICTS = orig
        le.DIRECTION_ACTS = orig_d
    restored = ("closes nothing" in fine()) and ("acting exit" not in phrase())
    return flipped and restored, "flipped=%s restored=%s" % (flipped, restored)
check("the copy is derived from the engine, not reworded by hand", t_actually_derived)

def t_unlabelled_verdict_raises():
    """A v4 acting rule with no card label must fail loudly, not render silence."""
    fn = getattr(th, "acting_rules", None)
    if fn is None:
        return False, "acting_rules missing"
    orig = getattr(le, "ACTING_VERDICTS", ())
    try:
        le.ACTING_VERDICTS = tuple(orig) + ("SOME_V4_RULE",)
        try:
            fn()
            return False, "did not raise on an unlabelled acting verdict"
        except KeyError as e:
            return "SOME_V4_RULE" in str(e), "raised %s" % e
    finally:
        le.ACTING_VERDICTS = orig
check("an acting verdict with no label raises rather than rendering nothing", t_unlabelled_verdict_raises)

# ---- 10  whole-card sweep, open and closed ---------------------------------
def _card_for(sql):
    con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    con.row_factory = sqlite3.Row
    row = con.execute(sql).fetchone()
    if row is None:
        return None, None
    pos = dict(row)
    pos["status"] = "CLOSED" if pos.get("closed_at") else "OPEN"
    checks = [dict(r) for r in con.execute(
        "select * from checks where position_id=? and data_quality='GOOD' "
        "order by checked_at", (pos["id"],))]
    legs = [dict(r) for r in con.execute(
        "select * from legs where position_id=?", (pos["id"],))]
    et = le.entry_thesis(con, pos["id"])
    return th.evaluate(pos, legs, checks, entry_thesis_row=et), pos

def _all_long_cards():
    con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute("select * from positions where strategy in "
                       "('LONG_CALL','LONG_PUT') order by opened_at").fetchall()
    for row in rows:
        pos = dict(row)
        pos["status"] = "CLOSED" if pos.get("closed_at") else "OPEN"
        checks = [dict(r) for r in con.execute(
            "select * from checks where position_id=? and data_quality='GOOD' "
            "order by checked_at", (pos["id"],))]
        legs = [dict(r) for r in con.execute(
            "select * from legs where position_id=?", (pos["id"],))]
        et = le.entry_thesis(con, pos["id"])
        yield th.evaluate(pos, legs, checks, entry_thesis_row=et), pos


def t_sweep_all():
    """Every long card in both books, open and closed.

    Sweeping ONE card proves nothing: the belief's acting claim renders only
    when the position has an entry thesis AND a journalled arm, so a card like
    APLD (never armed) walks straight past the defect and reports clean. The
    first version of this suite did exactly that and passed against the
    unfixed code. So this asserts BRANCH COVERAGE too -- it fails if no card
    exercised the graded path, and again if none exercised the broken path,
    which is where 'this is the acting exit' lives.
    """
    offenders, graded, broken, n = [], 0, 0, 0
    for card, pos in _all_long_cards():
        n += 1
        for b in card.get("beliefs") or []:
            if b.get("key") != "direction":
                continue
            if b.get("state") not in (th.UNKNOWN, th.PARTIAL):
                graded += 1
            if b.get("state") in (th.BROKEN, th.BROKEN_LOUD):
                broken += 1
        for path, s in walk_strings(card):
            for pat in BAD_PATTERNS:
                if re.search(pat, s):
                    offenders.append("%s %s: %s" % (pos["ticker"], path, s[:70]))
    if not n:
        return False, "no long positions to sweep"
    if not graded:
        return False, ("swept %d cards, none rendered a GRADED direction belief -- "
                       "the branch was never exercised, so a pass is meaningless" % n)
    if not broken:
        return False, ("swept %d cards, none was BROKEN -- the acting-exit line "
                       "never rendered, so a pass is meaningless" % n)
    return (not offenders), "%d cards (%d graded, %d broken), offenders=%r" % (
        n, graded, broken, offenders[:3])

check("no string on ANY long card, open or closed, claims the direction belief acts",
      t_sweep_all)

def t_no_register_refs():
    card, pos = _card_for("select * from positions where strategy='LONG_CALL' "
                          "and closed_at is null limit 1")
    if card is None:
        return False, "no position"
    hits = []
    for path, s in walk_strings(card):
        for m in REGISTER_REF.finditer(s):
            hits.append("%s: %s" % (path, m.group(0)))
    return (not hits), "hits=%r" % (hits[:5],)
check("no register reference on any card string (the standing copy rule)", t_no_register_refs)

def t_card_carries_doctrine():
    card, _pos = _card_for("select * from positions where strategy='LONG_CALL' "
                           "and closed_at is null limit 1")
    if card is None:
        return False, "no position"
    d = card.get("doctrine")
    return bool(d) and "Doctrine:" in d, "%r" % ((d or "")[:120],)
check("the card carries its doctrine sentence (template no longer hardcodes it)",
      t_card_carries_doctrine)

# ---------------------------------------------------------------------------
print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
for n, d in PASS:
    print("  ok   %s -- %s" % (n, d))
for n, d in FAIL:
    print("  FAIL %s -- %s" % (n, d))
sys.exit(1 if FAIL else 0)
