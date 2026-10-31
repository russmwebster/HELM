"""helm/fillimport.py -- the Fidelity import, stage 1 (s136, 2026-10-26).

Rulebook v0.2 section 12.1, as Russ decided it on 10-26:
  * every option trade in the Rollover IRA is HELM's;
  * fills dated on or after CUTOFF (2026-10-24, the start of no double entry)
    are booked; earlier ones are only compared and reported;
  * OPEN positions get their entry prices corrected to Fidelity's fills (shown
    first, written on the one confirm); CLOSED positions are never corrected.

Stage 1 books closes, leg closes and expiries; s138 adds ASSIGNED short puts
(the option row and its "YOU BOUGHT ASSIGNED PUTS" share row, booked by
helm/assignment.py). s141 (stage 2) books new opens and rolls (helm/fillopens.py).
s142 books PARTIAL closes of a one-leg put or covered call by splitting the
position: the closed contracts become their own CLOSED position (tag
split_from:<id>, its share of the premium, closed through _finalize_close), the
original stays OPEN with the rest -- its leg, total_contracts and net_premium cut
to what is left; its readings and entry snapshot stay with it. Every reader of an
open position keeps seeing an ordinary one-leg trade. Same-day pieces of one
order were already merged into one fill (brokerfills.merge_option_fills).
It REPORTS, and does not book, called-away shares, exercises, partial assignments
and share/dividend rows (later in stage 2).

    plan(conn, exports, ...)  -> Plan        read-only
    apply(plan, db_path)      -> list[str]   writes; the CLI takes the backup

Every booking goes through close_cmd's own primitives (close_leg,
_finalize_close) with when=<trade date>T16:00:00 -- the export has no time,
so the time is labelled "date only" in the leg's notes. Each fill row that
is booked, matched or used for a correction is recorded in broker_fills by
its key, so a second run on the same (or an overlapping) download proposes
nothing. Commission and fees are stored there per row -- net P&L is stage 3.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime

from helm import brokerfills as B

CUTOFF = "2026-10-24"
PRICE_TOL = 0.005          # a cent rounds; anything at or above half a cent is a difference
FILL_TIME = "16:00:00"
SINGLE_LEG = ("CSP", "COVERED_CALL", "LONG_CALL", "LONG_PUT")

BROKER_FILLS_DDL = """
CREATE TABLE IF NOT EXISTS broker_fills (
    fill_key     TEXT PRIMARY KEY,        -- brokerfills._fill_keys: row content + occurrence
    source       TEXT NOT NULL,           -- HISTORY_FOR_ACCOUNT | ACCOUNTS_HISTORY
    as_of        TEXT NOT NULL,           -- the trade date (as-of text preferred over Run Date)
    run_date     TEXT,
    action       TEXT NOT NULL,           -- the row's Action text (no account number in it)
    symbol       TEXT NOT NULL,
    qty          INTEGER,
    price        REAL,
    commission   REAL,
    fees         REAL,
    amount       REAL,
    outcome      TEXT NOT NULL CHECK (outcome IN ('BOOKED','MATCHED','CORRECTED')),
    position_id  TEXT,                    -- no foreign key: a fill record must never cascade
    leg_id       TEXT,
    imported_at  TEXT NOT NULL,
    note         TEXT
)"""


def _has_table(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def known_keys(conn):
    if not _has_table(conn, "broker_fills"):
        return set()
    return {r[0] for r in conn.execute("SELECT fill_key FROM broker_fills")}


def stored_ira_hash(conn):
    r = conn.execute("SELECT value FROM helm_meta WHERE key=?", (B.ACCOUNT_HASH_KEY,)).fetchone()
    return r[0] if r else None


@dataclass
class Item:
    kind: str                 # LEG_CLOSE | POSITION_CLOSE | PARTIAL_CLOSE | PRICE_FIX | MATCHED | ASSIGN | OPEN | REPORT
    text: str                 # one plain line for the trader
    position_id: str = None
    ticker: str = None
    legs: list = field(default_factory=list)      # [(leg_id, price, leg_reason)]
    reason: str = None        # position exit reason (POSITION_CLOSE)
    pnl: float = None
    as_of: str = None
    fills: list = field(default_factory=list)     # [(fill, key, source)] recorded on apply
    fix: dict = None          # PRICE_FIX: {leg_id, old, new, net_premium_old, net_premium_new}
    open: dict = None         # OPEN (s141): {strategy, legs, contracts, flags, grade, parent}
    split: dict = None        # PARTIAL_CLOSE (s142): {leg_id, have, close}; POSITION_CLOSE after one: {leg_id, have}


@dataclass
class Plan:
    files: list
    items: list
    history: list             # pre-cutoff differences, report only
    learned_hash: str = None  # the IRA hash, when this run learned it
    notes: list = field(default_factory=list)

    @property
    def writes(self):
        return [i for i in self.items if i.kind in ("LEG_CLOSE", "POSITION_CLOSE", "PARTIAL_CLOSE", "PRICE_FIX",
                                                    "MATCHED", "ASSIGN",
                                                    "OPEN")]


def _otype(leg):
    t = (leg["option_type"] or "").upper()
    if t in ("PUT", "CALL"):
        return t
    role = (leg["leg_role"] or "").upper()
    return "PUT" if "PUT" in role else ("CALL" if "CALL" in role else None)


def _contract(leg):
    return (leg["ticker"], (leg["expiration"] or "")[:10], _otype(leg), float(leg["strike"]), leg["direction"])


def _leg_pnl(direction, open_price, close_price, contracts, mult):
    if direction == "SHORT":
        return (open_price - close_price) * contracts * mult
    return (close_price - open_price) * contracts * mult


def _real_legs(conn):
    return conn.execute(
        "SELECT l.*, p.ticker, p.status AS pstatus, p.strategy FROM legs l "
        "JOIN positions p ON p.id = l.position_id "
        "WHERE p.book='REAL' AND l.expiration IS NOT NULL AND l.strike IS NOT NULL "
        "ORDER BY l.open_date, l.id").fetchall()


def _fmt(x):
    return "—" if x is None else "%.2f" % x


def _position_reason(strategy, closing, all_expired):
    """The exit reason the import proposes; --reason overrides it."""
    if all_expired:
        return "EXPIRED"
    if strategy in ("CSP", "COVERED_CALL") and len(closing) == 1:
        leg, price = closing[0]
        if leg["direction"] == "SHORT" and price <= 0.5 * leg["open_price"] + 1e-9:
            return "PROFIT_TARGET"
    return "DISCRETIONARY"


def _assign_item(conn, rows, row_keys, src_of, seen, label, tk, as_of, exp, strike, q, cands, fl):
    """s138: a short put ASSIGNED. Needs its share row -- "YOU BOUGHT ASSIGNED PUTS AS OF ..."
    on the same date, 100 x contracts shares at the strike -- and exactly one open real
    CSP leg that matches. Booked by helm/assignment.py; anything else is a REPORT."""
    shares = [(f, k) for f, k in zip(rows, row_keys)
              if not f.is_option and f.ticker == tk and f.as_of == as_of and k not in seen
              and "ASSIGNED PUTS" in (f.raw_action or "").upper()]
    n_sh = sum(int(f.qty or 0) for f, _ in shares)
    if not shares or n_sh != 100 * q:
        return Item("REPORT", "%s short put assigned -- its share row (%d shares expected, %d found) isn't in "
                    "this download; not booked" % (label, 100 * q, n_sh), ticker=tk)
    bad = [f for f, _ in shares if f.price is None or abs(f.price - float(strike)) >= PRICE_TOL]
    if bad:
        return Item("REPORT", "%s short put assigned -- the shares came in at %s, not the %g strike; not booked"
                    % (label, _fmt(bad[0].price), strike), ticker=tk)
    allf = fl + [(f, k, src_of[k]) for f, k in shares]
    done = [l for l in cands if l["status"] == "CLOSED" and (l["close_date"] or "")[:10] == as_of
            and l["strategy"] == "CSP"]
    if done and sum(l["contracts"] for l in done) == q:
        return Item("MATCHED", "%s assigned -- already booked in HELM" % label, ticker=tk,
                    position_id=done[0]["position_id"], fills=allf, as_of=as_of)
    live = [l for l in cands if l["status"] == "OPEN" and l["pstatus"] == "OPEN" and l["strategy"] == "CSP"]
    if len(live) != 1 or live[0]["contracts"] != q:
        return Item("REPORT", "%s short put assigned -- %d open HELM puts match (%s contracts); refused, "
                    "book by hand with `helm assign`" % (label, len(live), "/".join(str(l["contracts"]) for l in live)
                                                          or "none"), ticker=tk)
    from helm import assignment as A
    try:
        v = A.preview(conn, live[0]["position_id"])
    except ValueError as e:
        return Item("REPORT", "%s short put assigned -- %s; not booked" % (label, e), ticker=tk)
    return Item("ASSIGN", "%s ASSIGNED: put closes at 0.00 (premium $%s kept); %d %s shares in at %g; "
                "effective basis $%.2f a share" % (label, "{:,.2f}".format(v["premium"]), v["shares"], tk,
                                                    float(strike), v["effective_basis"]),
                position_id=live[0]["position_id"], ticker=tk, pnl=v["premium"], as_of=as_of, fills=allf,
                legs=[(live[0]["id"], 0.0, "ASSIGNED")])


PARTIAL_OK = ("CSP", "COVERED_CALL")


def _partial_item(conn, leg, q, price, event, label, tk, as_of, fl, reasons, left):
    """s142: Fidelity closed q of the leg's contracts. Booked as a split only for a
    one-leg put or covered call closed (not expired) for fewer contracts than HELM
    holds; anything else is a REPORT."""
    have = leg["contracts"]
    why = None
    if event != "CLOSE":
        why = "an expiry of %d of HELM's %d contracts -- the counts disagree" % (q, have)
    elif q > have:
        why = "Fidelity closed %d, HELM's leg has only %d" % (q, have)
    elif leg["strategy"] not in PARTIAL_OK:
        why = "a partial close on a %s isn't booked -- only puts and covered calls" % leg["strategy"].lower()
    elif conn.execute("SELECT COUNT(*) FROM legs WHERE position_id=? AND status='OPEN'",
                      (leg["position_id"],)).fetchone()[0] != 1:
        why = "the position has more than one open leg"
    if why:
        return Item("REPORT", "%s -- HELM's leg has %d contracts, Fidelity closed %d: %s; book by hand"
                    % (label, have, q, why), ticker=tk)
    pnl = _leg_pnl(leg["direction"], leg["open_price"], price, q, leg["multiplier"] or 100)
    one = dict(leg, contracts=q)
    reason = reasons.get(leg["position_id"]) or _position_reason(leg["strategy"], [(one, price)], False)
    left[leg["id"]] = have - q
    return Item("PARTIAL_CLOSE", "%s %s %s PARTLY CLOSED: %d of %d -- %s %g %s %s -> %s; realized $%s (reason %s); "
                "%d stay open" % (as_of, tk, leg["strategy"].lower().replace("_", " "), q, have,
                                  leg["direction"].lower(), leg["strike"], (leg["expiration"] or "")[:10],
                                  _fmt(leg["open_price"]), _fmt(price), "{:,.2f}".format(pnl), reason, have - q),
                position_id=leg["position_id"], ticker=tk, legs=[(leg["id"], price, "DISCRETIONARY")],
                reason=reason, pnl=pnl, as_of=as_of, fills=fl, split={"leg_id": leg["id"], "have": have, "close": q})


def split_off(conn, position_id, leg_id, have, k, now):
    """s142: move k of an open one-leg position's `have` contracts to a new OPEN
    position (same trade, its share of the premium), and cut the original to
    have - k. One transaction on `conn`; the caller commits, then closes the new
    position through close_cmd._finalize_close. Returns (new_pid, new_leg_id).
    Refuses unless the rows are exactly as planned."""
    from helm.models.position import Position
    from helm.models.leg import Leg
    p = conn.execute("SELECT * FROM positions WHERE id=?", (position_id,)).fetchone()
    l = conn.execute("SELECT * FROM legs WHERE id=?", (leg_id,)).fetchone()
    n_open = conn.execute("SELECT COUNT(*) FROM legs WHERE position_id=? AND status='OPEN'", (position_id,)).fetchone()[0]
    if (p is None or l is None or p["status"] != "OPEN" or l["status"] != "OPEN" or l["position_id"] != position_id
            or int(l["contracts"]) != int(have) or n_open != 1 or not (0 < k < have)):
        raise RuntimeError("%s: the position is no longer as planned (%s open, leg %s x%s) -- stopped"
                           % (position_id, p and p["status"], l and l["status"], l and l["contracts"]))
    npo = p["net_premium"]
    piece_np = None if npo is None else round(npo * k / have, 2)
    keep_np = None if npo is None else round(npo - piece_np, 2)
    new_pid = Position.new_id(p["ticker"], p["strategy"])
    new_lid = Leg.new_id(new_pid, l["leg_role"])
    tag = "split_from:%s" % position_id
    pr = dict(p)
    pr.update(id=new_pid, total_contracts=k, net_premium=piece_np, realized_pnl=None, signal_id=None,
              tags=(pr["tags"] + "," if pr["tags"] else "") + tag, created_at=now, updated_at=now,
              notes=((pr["notes"] + " | ") if pr["notes"] else "") + (
                  "Fidelity import %s: %d of %d contracts closed early, split off %s; its readings and entry "
                  "snapshot stay on %s" % (now[:10], k, have, position_id, position_id)))
    cols = list(pr)
    conn.execute("INSERT INTO positions (%s) VALUES (%s)" % (",".join(cols), ",".join("?" * len(cols))),
                 [pr[c] for c in cols])
    lr = dict(l)
    lr.update(id=new_lid, position_id=new_pid, contracts=k, created_at=now,
              notes=((lr["notes"] + " | ") if lr["notes"] else "") + "split from %s (%d of %d)" % (leg_id, k, have))
    cols = list(lr)
    conn.execute("INSERT INTO legs (%s) VALUES (%s)" % (",".join(cols), ",".join("?" * len(cols))),
                 [lr[c] for c in cols])
    conn.execute("UPDATE legs SET contracts=?, notes=COALESCE(notes || ' | ', '') || ? WHERE id=? AND status='OPEN'",
                 (have - k, "Fidelity import %s: %d of %d closed early, split off to %s" % (now[:10], k, have, new_pid),
                  leg_id))
    conn.execute("UPDATE positions SET total_contracts=?, net_premium=?, updated_at=?, "
                 "notes=COALESCE(notes || ' | ', '') || ? WHERE id=? AND status='OPEN'",
                 (have - k, keep_np, now, "Fidelity import %s: %d of %d contracts closed early -> %s; premium %s -> %s"
                  % (now[:10], k, have, new_pid, npo, keep_np), position_id))
    return new_pid, new_lid


def plan(conn, exports, cutoff=CUTOFF, reasons=None):
    """exports: [brokerfills.Export]. reasons: {position_id: EXIT_REASON}."""
    reasons = reasons or {}
    seen = known_keys(conn)
    items, history, notes = [], [], []

    # every row once, across overlapping downloads (same key = same row)
    rows, row_keys, row_src, dup = [], [], [], 0
    taken = set()
    for e in exports:
        for f, k in zip(e.fills, e.keys):
            if k in taken:
                dup += 1
                continue
            taken.add(k)
            rows.append(f); row_keys.append(k); row_src.append(e.fmt)
    if dup:
        notes.append("%d row(s) appear in more than one download -- counted once" % dup)
    src_of = dict(zip(row_keys, row_src))

    groups = B.merge_option_fills(rows, row_keys)
    legs = _real_legs(conn)

    by_contract = {}
    for l in legs:
        by_contract.setdefault(_contract(l), []).append(l)

    # ---------------------------------------------------- new fills (>= cutoff)
    closing = {}   # position_id -> [(leg, price, leg_reason, group)]
    left = {}      # s142: leg_id -> contracts still open after a partial close earlier in this run
    pending_opens = []   # s141: opening fills not in HELM -> helm/fillopens.classify
    for gk, g in sorted(groups.items(), key=lambda kv: (kv[0][1], kv[0][2])):
        event, as_of, tk, exp, otype, strike, direction = gk
        if as_of < cutoff:
            continue
        fresh = [(f, k) for f, k in zip(g["rows"], g["keys"]) if k not in seen]
        if not fresh:
            continue
        if len(fresh) != len(g["rows"]):
            items.append(Item("REPORT", "%s %s: part of this group was imported before -- "
                              "not booked again; check by hand" % (as_of, g["symbol"]), ticker=tk))
            continue
        if event in ("EXPIRED", "ASSIGNED", "EXERCISED"):
            direction = "SHORT" if sum(f.qty for f in g["rows"]) > 0 else "LONG"
        q = g["contracts"]
        label = "%s %s %s %s %s x%d" % (as_of, tk, exp, otype, "%g" % strike, q)
        cands = by_contract.get((tk, exp, otype, float(strike), direction), [])
        fl = [(f, k, src_of[k]) for f, k in fresh]

        if event == "ASSIGNED" and otype == "PUT" and direction == "SHORT":
            items.append(_assign_item(conn, rows, row_keys, src_of, seen, label, tk, as_of, exp, strike, q,
                                      cands, fl))
            continue
        if event in ("ASSIGNED", "EXERCISED"):
            items.append(Item("REPORT", "%s %s -- %s; %s booked in stage 2" % (
                label, direction.lower(), event.lower(),
                "called-away shares are" if otype == "CALL" else "exercises are"), ticker=tk))
            continue

        if event == "OPEN":
            same_day = [l for l in cands if (l["open_date"] or "")[:10] == as_of]
            if same_day and sum(l["contracts"] for l in same_day) == q:
                items.append(Item("MATCHED", "%s opened -- already in HELM" % label, ticker=tk,
                                  position_id=same_day[0]["position_id"], fills=fl, as_of=as_of))
            elif same_day:
                items.append(Item("REPORT", "%s %s opened -- HELM has %d contracts opened that day, Fidelity %d; "
                                  "not booked again, check by hand" % (label, direction.lower(),
                                                                      sum(l["contracts"] for l in same_day), q),
                                  ticker=tk))
            elif any(l["status"] == "OPEN" and l["pstatus"] == "OPEN" for l in cands):
                o = [l for l in cands if l["status"] == "OPEN" and l["pstatus"] == "OPEN"][0]
                items.append(Item("REPORT", "%s %s opened -- HELM already has this contract open (opened %s, %s); "
                                  "not booked again, check the dates by hand" % (
                                      label, direction.lower(), (o["open_date"] or "")[:10], o["position_id"]),
                                  ticker=tk))
            else:
                pending_opens.append((gk, g, fl))
            continue

        # CLOSE or EXPIRED
        price = 0.0 if event == "EXPIRED" else g["price"]
        already = [l for l in cands if l["status"] == "CLOSED" and (l["close_date"] or "")[:10] == as_of]
        if already and sum(l["contracts"] for l in already) == q:
            items.append(Item("MATCHED", "%s closed -- already in HELM" % label, ticker=tk,
                              position_id=already[0]["position_id"], fills=fl, as_of=as_of))
            continue
        live = [l for l in cands if l["status"] == "OPEN" and l["pstatus"] == "OPEN"]
        if any(l["id"] in left for l in live):   # s142: as this run's earlier partial leaves it
            live = [dict(l, contracts=left[l["id"]]) if l["id"] in left else l for l in live]
            live = [l for l in live if l["contracts"] > 0]
        exact = [l for l in live if l["contracts"] == q]
        if len(live) == 1 and len(exact) == 1:
            leg = exact[0]
            lr = "WORTHLESS" if event == "EXPIRED" else "DISCRETIONARY"
            closing.setdefault(leg["position_id"], []).append((leg, price, lr, g, fl, event))
        elif not live:
            items.append(Item("REPORT", "%s %s %s at %s -- no open HELM leg matches; not booked"
                              % (label, direction.lower(), event.lower(), _fmt(price)), ticker=tk))
        elif len(live) > 1:
            items.append(Item("REPORT", "%s -- %d open HELM legs could be meant; refused, book by hand"
                              % (label, len(live)), ticker=tk))
        else:
            items.append(_partial_item(conn, live[0], q, price, event, label, tk, as_of, fl, reasons, left))

    for pid, cl in closing.items():
        p = conn.execute("SELECT * FROM positions WHERE id=?", (pid,)).fetchone()
        open_legs = conn.execute("SELECT * FROM legs WHERE position_id=? AND status='OPEN'", (pid,)).fetchall()
        closing_ids = {c[0]["id"] for c in cl}
        whole = {l["id"] for l in open_legs} == closing_ids
        as_of = max(c[3]["rows"][0].as_of for c in cl)   # the day the position finished
        fills = [x for c in cl for x in c[4]]
        lines = []
        for leg, price, lr, g, fl, ev in cl:
            pnl = _leg_pnl(leg["direction"], leg["open_price"], price, leg["contracts"], leg["multiplier"] or 100)
            lines.append((leg, price, lr, pnl, ev, fl))
        if whole:
            done = conn.execute("SELECT * FROM legs WHERE position_id=? AND status='CLOSED' AND close_price IS NOT NULL",
                                (pid,)).fetchall()
            earlier = sum(_leg_pnl(l["direction"], l["open_price"], l["close_price"], l["contracts"],
                                   l["multiplier"] or 100) for l in done)
            total = earlier + sum(x[3] for x in lines)
            all_exp = all(x[4] == "EXPIRED" for x in lines)
            reason = reasons.get(pid) or _position_reason(p["strategy"], [(x[0], x[1]) for x in lines], all_exp)
            txt = "%s %s %s CLOSED: %s; realized $%s (%s)" % (
                as_of, p["ticker"], p["strategy"].lower().replace("_", " "),
                "; ".join("%s %s %g %s %s -> %s" % (l["direction"].lower(), _otype(l).lower(), l["strike"],
                                                      (l["expiration"] or "")[:10], _fmt(l["open_price"]), _fmt(pr))
                          for l, pr, _, _, _, _ in lines),
                "{:,.2f}".format(total), "reason " + reason + ("" if pid not in reasons else ", yours"))
            if earlier:
                txt += " -- includes $%s from legs closed earlier" % "{:,.2f}".format(earlier)
            items.append(Item("POSITION_CLOSE", txt, position_id=pid, ticker=p["ticker"],
                              legs=[(l["id"], pr, lr) for l, pr, lr, _, _, _ in lines], reason=reason,
                              pnl=total, as_of=as_of, fills=fills,
                              split={"after": {l["id"]: l["contracts"] for l, _, _, _, _, _ in lines
                                               if l["id"] in left}} if any(l["id"] in left for l, *_ in lines) else None))
        else:
            for l, pr, lr, pnl, ev, fl in lines:
                items.append(Item("LEG_CLOSE", "%s %s %s leg %s %g %s %s -> %s; $%s; position stays open" % (
                    as_of, p["ticker"], "expired:" if ev == "EXPIRED" else "close", l["direction"].lower(),
                    l["strike"], (l["expiration"] or "")[:10], _fmt(l["open_price"]), _fmt(pr),
                    "{:,.2f}".format(pnl)), position_id=pid, ticker=p["ticker"], legs=[(l["id"], pr, lr)],
                    pnl=pnl, as_of=as_of, fills=fl))

    # ------------------------------------------------- s141: new opens and rolls
    if pending_opens:
        from helm import fillopens as FO
        ctx = {"closed": {}, "closing_legs": set(), "assign_shares": {}}
        for it in items:
            if it.kind in ("POSITION_CLOSE", "LEG_CLOSE"):
                ctx["closing_legs"].update(lid for lid, _, _ in it.legs)
            if it.kind == "POSITION_CLOSE" and it.reason != "EXPIRED":
                p = conn.execute("SELECT strategy FROM positions WHERE id=?", (it.position_id,)).fetchone()
                if p and p["strategy"] in ("CSP", "COVERED_CALL"):
                    ctx["closed"][(it.as_of, it.ticker, "PUT" if p["strategy"] == "CSP" else "CALL")] = it.position_id
            if it.kind == "ASSIGN":
                n = conn.execute("SELECT contracts FROM legs WHERE id=?", (it.legs[0][0],)).fetchone()[0]
                ctx["assign_shares"][it.ticker] = ctx["assign_shares"].get(it.ticker, 0) + 100 * int(n)
        items.extend(FO.classify(conn, pending_opens, ctx))

    # ------------------------------------------- entry prices on OPEN positions
    opens = {}
    for gk, g in groups.items():
        if gk[0] == "OPEN":
            opens[(gk[2], gk[3], gk[4], float(gk[5]), gk[6], gk[1])] = g
    for l in legs:
        if l["pstatus"] != "OPEN":
            continue
        c = _contract(l)
        g = opens.get(c + ((l["open_date"] or "")[:10],))
        if g is None:
            continue
        helm_n = l["contracts"]
        sibs = [x for x in legs if x["id"] != l["id"] and _contract(x) == c
                and (x["open_date"] or "")[:10] == (l["open_date"] or "")[:10]]
        if g["contracts"] != helm_n and sibs:   # s142: the parts split off by a partial close
            helm_n += sum(x["contracts"] for x in sibs)
        if g["contracts"] != helm_n:
            items.append(Item("REPORT", "%s %s %g %s: HELM has %d contracts, Fidelity filled %d on %s -- not corrected"
                              % (l["ticker"], _otype(l).lower(), l["strike"], c[1], l["contracts"], g["contracts"],
                                 (l["open_date"] or "")[:10]), ticker=l["ticker"]))
            continue
        new = round(g["price"], 4)
        if abs(new - l["open_price"]) < PRICE_TOL:
            continue
        if sibs:
            items.append(Item("REPORT", "%s %s %g %s: opened at %s in HELM, %.4g at Fidelity -- part of it was split "
                              "off by a partial close; not corrected automatically" % (
                                  l["ticker"], _otype(l).lower(), l["strike"], c[1], _fmt(l["open_price"]), new),
                              ticker=l["ticker"]))
            continue
        fresh = [(f, k, src_of[k]) for f, k in zip(g["rows"], g["keys"]) if k not in seen]
        if l["status"] != "OPEN":
            items.append(Item("REPORT", "%s %s %s %g %s (closed leg on an open position): opened at %s in HELM, "
                              "%s at Fidelity -- not corrected" % (l["ticker"], l["direction"].lower(), _otype(l).lower(),
                                                                   l["strike"], c[1], _fmt(l["open_price"]),
                                                                   "%.4g" % new), ticker=l["ticker"]))
            continue
        p = conn.execute("SELECT * FROM positions WHERE id=?", (l["position_id"],)).fetchone()
        n_legs = conn.execute("SELECT COUNT(*) FROM legs WHERE position_id=?", (p["id"],)).fetchone()[0]
        if p["strategy"] not in SINGLE_LEG or n_legs != 1:
            items.append(Item("REPORT", "%s %s %g %s: opened at %s in HELM, %.4g at Fidelity -- a multi-leg "
                              "position; not corrected automatically" % (p["ticker"], _otype(l).lower(), l["strike"],
                                                                       c[1], _fmt(l["open_price"]), new),
                              ticker=p["ticker"]))
            continue
        mult = l["multiplier"] or 100
        npo = p["net_premium"]
        npn = round(new * l["contracts"] * mult, 2)
        expect_old = round(l["open_price"] * l["contracts"] * mult, 2)
        if npo is not None and abs(npo - expect_old) > 0.011:
            items.append(Item("REPORT", "%s: stored premium $%.2f is not its leg's $%.2f -- refused, look by hand"
                              % (p["id"], npo, expect_old), ticker=p["ticker"]))
            continue
        items.append(Item("PRICE_FIX", "%s %s %s %g %s: entry %s -> %s (Fidelity's fill on %s); premium $%s -> $%s"
                          % (p["ticker"], p["strategy"].lower().replace("_", " "), _otype(l).lower(), l["strike"], c[1],
                             _fmt(l["open_price"]), _fmt(new), (l["open_date"] or "")[:10],
                             "{:,.2f}".format(npo or 0), "{:,.2f}".format(npn)),
                          position_id=p["id"], ticker=p["ticker"], fills=fresh,
                          fix={"leg_id": l["id"], "old": l["open_price"], "new": new,
                               "net_premium_old": npo, "net_premium_new": npn}))

    # --------------------------------------- history (before cutoff), report only
    hl = {}
    for l in legs:
        for ev, d, pr in (("OPEN", l["open_date"], l["open_price"]), ("CLOSE", l["close_date"], l["close_price"])):
            if d and pr is not None and (ev == "OPEN" or l["status"] == "CLOSED"):
                k = (ev, d[:10]) + _contract(l)[:4] + (l["direction"],)
                h = hl.setdefault(k, [0, 0.0])
                h[0] += l["contracts"]; h[1] += l["contracts"] * pr
    lo = min((gk[1] for gk in groups), default=cutoff)
    for gk, g in groups.items():
        if gk[1] >= cutoff or gk[0] not in ("OPEN", "CLOSE"):
            continue
        h = hl.get(gk)
        name = "%s %s %s %s %g %s" % (gk[1], gk[0].lower(), gk[2], gk[4].lower(), gk[5], gk[3])
        if h is None:
            history.append("%s x%d at %s -- not in HELM on that date" % (name, g["contracts"], _fmt(g["price"])))
        elif h[0] != g["contracts"]:
            history.append("%s -- HELM %d contracts, Fidelity %d" % (name, h[0], g["contracts"]))
        elif abs(h[1] / h[0] - g["price"]) >= PRICE_TOL:
            history.append("%s -- HELM %.4g, Fidelity %.4g" % (name, h[1] / h[0], g["price"]))
    for k, h in hl.items():
        if lo <= k[1] < cutoff and k not in groups and k[0] in ("OPEN", "CLOSE"):
            history.append("%s %s %s %s %g %s x%d -- in HELM, not at Fidelity on that date"
                           % (k[1], k[0].lower(), k[2], k[4].lower(), k[5], k[3], h[0]))
    history.sort()
    return Plan(files=[e.path for e in exports], items=items, history=history, notes=notes)


def _record(conn, fills, outcome, position_id, leg_id, note, now):
    for f, k, src in fills:
        conn.execute(
            "INSERT OR IGNORE INTO broker_fills (fill_key, source, as_of, run_date, action, symbol, qty, price, "
            "commission, fees, amount, outcome, position_id, leg_id, imported_at, note) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (k, src, f.as_of, f.run_date, f.raw_action, f.symbol.strip(), f.qty, f.price, f.commission,
             f.fees, f.amount, outcome, position_id, leg_id, now, note))


def ensure_schema(conn, ira_hash):
    """broker_fills + the IRA's account hash. Refuses a different hash."""
    conn.execute(BROKER_FILLS_DDL)
    have = stored_ira_hash(conn)
    if have is None and ira_hash:
        conn.execute("INSERT INTO helm_meta (key, value) VALUES (?, ?)", (B.ACCOUNT_HASH_KEY, ira_hash))
    elif have is not None and ira_hash and have != ira_hash:
        raise RuntimeError("this download's Rollover IRA is not the account HELM already knows -- refused")


def apply(p, db_path=None, ira_hash=None):
    """Write the plan. The caller has taken the backup. Each booking goes through
    close_cmd's primitives; each re-checks its legs are still as planned."""
    from helm.db import get_conn
    from helm.models.position import Position
    from helm.models.leg import Leg
    from helm.cli import close_cmd as C

    now = datetime.now().isoformat(timespec="seconds")
    done = []
    conn = get_conn(db_path) if db_path else get_conn()
    try:
        ensure_schema(conn, ira_hash)
        conn.commit()
    finally:
        conn.close()

    recorded = set()
    for it in p.writes:
        when = "%sT%s" % (it.as_of, FILL_TIME) if it.as_of else None
        label = "Fidelity import %s: price and date from the fill; the export has no time (%s used, date only)" % (
            now[:10], FILL_TIME[:5])
        if it.kind in ("LEG_CLOSE", "POSITION_CLOSE"):
            pos = Position.get(it.position_id)
            legs = Leg.for_position(it.position_id)
            byid = {l.id: l for l in legs}
            for lid, _, _ in it.legs:
                if byid.get(lid) is None or str(byid[lid].status).upper() != "OPEN":
                    raise RuntimeError("%s: leg %s is no longer open -- stopped; nothing more written" % (it.ticker, lid))
                exp_n = ((it.split or {}).get("after") or {}).get(lid)
                if exp_n is not None and int(byid[lid].contracts) != int(exp_n):
                    raise RuntimeError("%s: leg %s has %s contracts, the plan expected %s -- stopped"
                                       % (it.ticker, lid, byid[lid].contracts, exp_n))
            if it.kind == "POSITION_CLOSE":
                res = C._finalize_close(pos, legs, {lid: pr for lid, pr, _ in it.legs}, it.reason, when=when)
            else:
                lid, pr, lr = it.legs[0]
                res = C.close_leg(pos, legs, byid[lid], pr, reason=lr, when=when)
            if not res.get("ok"):
                raise RuntimeError("%s: %s -- stopped" % (it.ticker, res.get("error")))
            for lid, _, _ in it.legs:
                leg = Leg.get(lid)
                leg.notes = ((leg.notes + " | ") if leg.notes else "") + label
                leg.save()
        elif it.kind == "PARTIAL_CLOSE":
            sp = it.split
            c2 = get_conn(db_path) if db_path else get_conn()
            try:
                new_pid, new_lid = split_off(c2, it.position_id, sp["leg_id"], sp["have"], sp["close"], now)
                c2.commit()
            except Exception:
                c2.rollback()
                raise
            finally:
                c2.close()
            piece = Position.get(new_pid)
            res = C._finalize_close(piece, Leg.for_position(new_pid), {new_lid: it.legs[0][1]}, it.reason, when=when)
            if not res.get("ok"):
                raise RuntimeError("%s: the split-off part %s did not close (%s) -- stopped; it is OPEN, close it "
                                   "by hand or restore the backup" % (it.ticker, new_pid, res.get("error")))
            leg = Leg.get(new_lid)
            leg.notes = ((leg.notes + " | ") if leg.notes else "") + label
            leg.save()
            it.position_id = new_pid
            it.legs = [(new_lid, it.legs[0][1], it.legs[0][2])]
        elif it.kind == "ASSIGN":
            from helm import assignment as A
            c2 = get_conn(db_path) if db_path else get_conn()
            try:
                lid = it.legs[0][0]
                if c2.execute("SELECT status FROM legs WHERE id=?", (lid,)).fetchone()[0] != "OPEN":
                    raise RuntimeError("%s: the put is no longer open -- stopped; nothing more written" % it.ticker)
                A.book(c2, it.position_id, when, label)
            finally:
                c2.close()
        elif it.kind == "OPEN":
            from helm import fillopens as FO
            pid, lids, _lab = FO.book(it, now[:10], FILL_TIME)
            it.position_id = pid
            it.legs = [(lids[0], None, None)] if len(lids) == 1 else [(x, None, None) for x in lids]
        elif it.kind == "PRICE_FIX":
            fx = it.fix
            leg = Leg.get(fx["leg_id"])
            pos = Position.get(it.position_id)
            if abs(leg.open_price - fx["old"]) > 1e-9 or pos.status != "OPEN":
                raise RuntimeError("%s: entry changed since the plan -- stopped" % it.ticker)
            leg.open_price = fx["new"]
            leg.notes = ((leg.notes + " | ") if leg.notes else "") + (
                "Fidelity import %s: entry corrected %s -> %s from the fill" % (now[:10], _fmt(fx["old"]), _fmt(fx["new"])))
            leg.save()
            pos.net_premium = fx["net_premium_new"]
            pos.notes = ((pos.notes + " | ") if pos.notes else "") + (
                "Fidelity import %s: premium %s -> %s (entry price from the fill)" % (
                    now[:10], fx["net_premium_old"], fx["net_premium_new"]))
            pos.save()
        conn = get_conn(db_path) if db_path else get_conn()
        try:
            fills = [x for x in it.fills if x[1] not in recorded]
            outcome = {"MATCHED": "MATCHED", "PRICE_FIX": "CORRECTED"}.get(it.kind, "BOOKED")
            lid = it.legs[0][0] if len(it.legs) == 1 else (it.fix or {}).get("leg_id")
            _record(conn, fills, outcome, it.position_id, lid, it.kind, now)
            conn.commit()
            recorded.update(x[1] for x in fills)
        finally:
            conn.close()
        done.append(it.text)

    conn = get_conn(db_path) if db_path else get_conn()
    try:
        conn.execute("INSERT INTO helm_events (id, event_type, entity_name, occurred_at, notes) VALUES (?,?,?,?,?)",
                     ("import-" + now.replace(":", ""), "IMPORT_RUN", "fidelity", now,
                      "%d written from %s" % (len(done), ", ".join(B.mask_name(x.split("/")[-1]) for x in p.files))))
        conn.commit()
    finally:
        conn.close()
    return done
