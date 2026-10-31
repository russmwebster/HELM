#!/usr/bin/env python3
"""
HELM-002 constraint/default/FK diagnostic (READ-ONLY).

The standing gate (apply_schema_reconcile.py) reconciles *presence*: table set,
column-name set, indexes. It does NOT compare CHECK clauses, column DEFAULT / NOT
NULL / type / PK, foreign keys, or UNIQUE constraints. This script measures exactly
that residual surface so the HELM-002 constraints/defaults/FK pass can be scoped.

Method (mirrors the gate's idiom): execute the whole schema.sql into a fresh
in-memory DB (CREATE blocks + trailing ALTER ADD COLUMN migrations), then for every
table present in BOTH live and the fresh build, diff:

  1. columns   : (type, notnull, dflt_value, pk) per column          [PRAGMA table_info]
  2. fks       : (table, from, to, on_update, on_delete, match)      [PRAGMA foreign_key_list]
  3. uniqueness: UNIQUE / PK index column-tuples                     [PRAGMA index_list/index_info]
  4. checks    : normalized CHECK(...) clauses + quoted-literal sets [sqlite_master.sql]

It NEVER writes: live is opened read-only (mode=ro), the fresh build is :memory:.
Tables present on only one side are reported but are the presence gate's job, not this.

Usage:
  python3 diag_schema_constraints.py [helm/schema.sql] --db data/helm.db
"""
import argparse, os, sys, re, sqlite3


# ---------- helpers ----------

def tnames(conn):
    q = "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    return sorted(r[0] for r in conn.execute(q))


def table_info(conn, t):
    # name -> (type, notnull, dflt, pk)
    out = {}
    for cid, name, ctype, notnull, dflt, pk in conn.execute('PRAGMA table_info("%s")' % t):
        out[name] = ((ctype or "").upper().strip(), int(notnull), dflt, int(pk))
    return out


def fk_list(conn, t):
    # set of normalized fk tuples (order-insensitive across a table)
    out = set()
    for row in conn.execute('PRAGMA foreign_key_list("%s")' % t):
        # id, seq, table, from, to, on_update, on_delete, match
        _id, _seq, ftab, ffrom, fto, on_upd, on_del, match = row
        out.add((ftab, ffrom, fto, (on_upd or "").upper(),
                 (on_del or "").upper(), (match or "").upper()))
    return out


def unique_sets(conn, t):
    # set of (origin, frozenset(columns)) for UNIQUE ('u') and PK ('pk') indexes
    out = set()
    for row in conn.execute('PRAGMA index_list("%s")' % t):
        # seq, name, unique, origin, partial  (cols may vary by sqlite version)
        name = row[1]
        origin = row[3] if len(row) > 3 else "c"
        if origin not in ("u", "pk"):
            continue
        cols = tuple(r[2] for r in conn.execute('PRAGMA index_info("%s")' % name))
        out.add((origin, frozenset(cols)))
    return out


def create_sql(conn, t):
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (t,)
    ).fetchone()
    return row[0] if row and row[0] else ""


def extract_checks(create_text):
    """Return a list of normalized CHECK(...) clause bodies (balanced parens)."""
    checks = []
    s = create_text
    for m in re.finditer(r"\bCHECK\s*\(", s, flags=re.IGNORECASE):
        i = m.end() - 1  # position of the opening paren
        depth = 0
        j = i
        while j < len(s):
            if s[j] == "(":
                depth += 1
            elif s[j] == ")":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        body = s[i + 1:j]
        checks.append(re.sub(r"\s+", " ", body).strip())
    return checks


def quoted_literals(text):
    return set(re.findall(r"'([^']*)'", text))


def canon_check(body):
    """Canonical form of a CHECK body: structural skeleton (literals masked,
    whitespace + comma/paren spacing normalized, upper-cased) paired with the
    sorted set of quoted literals. Two IN-lists with the same literal set in any
    order / any whitespace canonicalize identically, but an operator swap, a
    different column, or a changed literal set does not."""
    lits = tuple(sorted(set(re.findall(r"'([^']*)'", body))))
    skel = re.sub(r"'[^']*'", "Q", body)        # mask string literals
    skel = re.sub(r"\s+", " ", skel)
    skel = re.sub(r"\s*([(),])\s*", r"\1", skel)  # tighten around parens/commas
    return (skel.strip().upper(), lits)


# ---------- diff ----------

def diff_table(t, live, mem):
    notes = []

    lci, mci = table_info(live, t), table_info(mem, t)
    for col in sorted(set(lci) & set(mci)):
        if lci[col] != mci[col]:
            lt, ln, ld, lp = lci[col]
            mt, mn, md, mp = mci[col]
            parts = []
            if lt != mt:
                parts.append("type live=%r schema=%r" % (lt, mt))
            if ln != mn:
                parts.append("notnull live=%d schema=%d" % (ln, mn))
            if (ld is None) != (md is None) or str(ld) != str(md):
                parts.append("default live=%r schema=%r" % (ld, md))
            if lp != mp:
                parts.append("pk live=%d schema=%d" % (lp, mp))
            if parts:
                notes.append("  col %-28s %s" % (col, " | ".join(parts)))

    lfk, mfk = fk_list(live, t), fk_list(mem, t)
    for fk in sorted(lfk - mfk):
        notes.append("  fk  live-only  %s" % (fk,))
    for fk in sorted(mfk - lfk):
        notes.append("  fk  schema-only %s" % (fk,))

    luq, muq = unique_sets(live, t), unique_sets(mem, t)
    for u in sorted(luq - muq, key=lambda x: (x[0], sorted(x[1]))):
        notes.append("  uniq live-only  %s(%s)" % (u[0], ",".join(sorted(u[1]))))
    for u in sorted(muq - luq, key=lambda x: (x[0], sorted(x[1]))):
        notes.append("  uniq schema-only %s(%s)" % (u[0], ",".join(sorted(u[1]))))

    lchecks = extract_checks(create_sql(live, t))
    mchecks = extract_checks(create_sql(mem, t))
    lcanon = {canon_check(c): c for c in lchecks}
    mcanon = {canon_check(c): c for c in mchecks}
    if set(lcanon) != set(mcanon):
        only_live = [lcanon[k] for k in (set(lcanon) - set(mcanon))]
        only_schema = [mcanon[k] for k in (set(mcanon) - set(lcanon))]
        lit_live = quoted_literals(" ".join(lchecks))
        lit_schema = quoted_literals(" ".join(mchecks))
        missing_lits = sorted(lit_live - lit_schema)
        extra_lits = sorted(lit_schema - lit_live)
        notes.append("  CHECK clauses differ (order/whitespace-insensitive):")
        for c in sorted(only_live):
            notes.append("      live-only : CHECK(%s)" % c)
        for c in sorted(only_schema):
            notes.append("      schema-only: CHECK(%s)" % c)
        if missing_lits:
            notes.append("      >> literals in LIVE check but MISSING from schema: %s" % missing_lits)
        if extra_lits:
            notes.append("      >> literals in SCHEMA check but absent from live: %s" % extra_lits)

    return notes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("schema", nargs="?", default="helm/schema.sql")
    ap.add_argument("--db", default="data/helm.db")
    args = ap.parse_args()

    if not os.path.exists(args.schema):
        sys.exit("schema file not found: %s" % args.schema)
    if not os.path.exists(args.db):
        sys.exit("db file not found: %s" % args.db)

    # live: read-only
    live = sqlite3.connect("file:%s?mode=ro" % os.path.abspath(args.db), uri=True)

    # fresh build from schema.sql
    with open(args.schema) as f:
        schema_text = f.read()
    mem = sqlite3.connect(":memory:")
    try:
        mem.executescript(schema_text)
    except sqlite3.Error as e:
        sys.exit("schema.sql failed to execute into a fresh DB: %s" % e)

    lt, mt = set(tnames(live)), set(tnames(mem))
    only_live, only_schema = sorted(lt - mt), sorted(mt - lt)
    both = sorted(lt & mt)

    print("=== HELM-002 constraint/default/FK diagnostic ===")
    print("live db   : %s" % args.db)
    print("schema    : %s" % args.schema)
    print("tables    : live=%d schema=%d both=%d" % (len(lt), len(mt), len(both)))
    if only_live:
        print("PRESENCE (gate's job) tables live-only : %s" % only_live)
    if only_schema:
        print("PRESENCE (gate's job) tables schema-only: %s" % only_schema)
    print("")

    total = 0
    for t in both:
        notes = diff_table(t, live, mem)
        if notes:
            total += len(notes)
            print("TABLE %s" % t)
            for n in notes:
                print(n)
            print("")

    if total == 0:
        print("CLEAN — no constraint/default/FK/CHECK/UNIQUE divergences on shared tables.")
    else:
        print("DIVERGENCES: %d note(s) across shared tables (see above)." % total)

    live.close()
    mem.close()


if __name__ == "__main__":
    main()
