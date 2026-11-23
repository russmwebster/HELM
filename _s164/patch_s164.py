"""s164 (Russ, 2026-11-22): the display-only heads-up when a sold put's buy-back (the stored mid) reaches twice the
premium taken in. Patches an ENGINE tree and a BOARD tree given as arguments (never the live folders directly).
Each replacement must match exactly once.  Usage: python3 patch_s164.py ENG_DIR PG_DIR"""
import sys
ENG, PG = sys.argv[1], sys.argv[2]

def patch(path, pairs):
    s = open(path, encoding="utf-8").read()
    for old, new in pairs:
        assert s.count(old) == 1, "%s: expected one match for %r, found %d" % (path, old[:70], s.count(old))
        s = s.replace(old, new)
    open(path, "w", encoding="utf-8").write(s)
    print("patched", path)

# ---------------- engine: helm/advice.py
patch(ENG + "/helm/advice.py", [
 ('NEAR_FRAC = 0.90            # s155 (Russ, 2026-11-08): "near a limit" = 90% of it, shown amber on the board\n',
  'NEAR_FRAC = 0.90            # s155 (Russ, 2026-11-08): "near a limit" = 90% of it, shown amber on the board\n'
  'PREMIUM_HEADSUP_X = 2.0     # s164 (Russ, 2026-11-22): a sold put\'s buy-back (mid) at this many times the premium taken\n'
  '                            # in is shown amber on the board -- display only: no verb, no advice text, no stop\n'),
 ('def _num(x):\n',
  'def premium_row(credit, mark):\n'
  '    """s164 (Russ, 2026-11-22): one open put\'s buy-back against PREMIUM_HEADSUP_X times the premium taken in, as a\n'
  '    row: "near" (amber, reached) at or above the line, else "next" with the buy-back price of the line. A heads-up,\n'
  '    not a rule: it is in no verb map, so the advice and its text never change because of it. Pure; per share."""\n'
  '    line = PREMIUM_HEADSUP_X * credit\n'
  '    reached = mark is not None and mark >= line - 1e-9\n'
  '    return {"key": "premium_2x", "rule": "Buy-back at %g× the premium" % PREMIUM_HEADSUP_X,\n'
  '            "state": "near" if reached else "next",\n'
  '            "reading": "mid %s, sold %s" % (_p(mark), _p(credit)) if mark is not None else "no mid",\n'
  '            "line": "buy-back %s" % _p(line)}\n'
  '\n'
  '\n'
  'def _num(x):\n'),
 ('                            earnings=e_day.isoformat() if (is_cc and e_inside) else None)\n',
  '                            earnings=e_day.isoformat() if (is_cc and e_inside) else None)\n'
  '    if not is_cc and credit:\n'
  '        rules.append(premium_row(credit, mark))      # s164: display only\n'),
 ('    print("advice selftest:", "PASS (%d checks)" % len(ran) if not fails else "%d FAIL" % len(fails))\n',
  '    # s164: the display-only line at twice the premium\n'
  '    ok(premium_row(0.42, 0.84)["state"] == "near" and premium_row(0.42, 0.8399)["state"] == "next",\n'
  '       "s164: the 2x line: reached at exactly twice, not a cent under")\n'
  '    ok(premium_row(0.42, 0.10)["line"] == "buy-back $0.84" and premium_row(19.47, 76.70)["reading"] == "mid $76.70, sold $19.47",\n'
  '       "s164: the line\'s price and the reading: %s" % premium_row(19.47, 76.70))\n'
  '    ok(premium_row(1.0, None)["state"] == "next", "s164: no mid -> not reached")\n'
  '    ok("premium_2x" not in SHORT_RULE_VERB and "premium_2x" not in PMCC_ROW_VERB, "s164: the line is in no verb map")\n'
  '    _sh = [t for t in R if R[t]["strategy"] == "CSP" and R[t]["verb"] != "STALE"]\n'
  '    ok(_sh and all("premium_2x" in rows(t) for t in _sh), "s164: every read open put carries the line: %s" % _sh)\n'
  '    ok(all("premium_2x" not in rows(t) for t in R if R[t]["strategy"] != "CSP"), "s164: nothing but a put carries it")\n'
  '    print("advice selftest:", "PASS (%d checks)" % len(ran) if not fails else "%d FAIL" % len(fails))\n'),
])

# ---------------- engine: helm/boardview.py (the Reference page's constant)
patch(ENG + "/helm/boardview.py", [
 ('"near": pct(A.NEAR_FRAC), "passing": ", ".join(A.PASSING), "stale_days": A.STALE_DAYS,\n',
  '"near": pct(A.NEAR_FRAC), "passing": ", ".join(A.PASSING), "stale_days": A.STALE_DAYS,\n'
  '        "premium_x": "%g×" % A.PREMIUM_HEADSUP_X,      # s164\n'),
])

# ---------------- engine: helm/todayview.py (the mark on Today's position line)
patch(ENG + "/helm/todayview.py", [
 ('            fired = [x for x in rows if x["state"] == "fired"]\n'
  '            row = {"position_id": pid, "ticker": p["ticker"], "strategy": p["strategy"], "label": label,\n',
  '            fired = [x for x in rows if x["state"] == "fired"]\n'
  '            hu = next((x for x in rows if x["key"] == "premium_2x" and x["state"] == "near"), None)   # s164\n'
  '            row = {"position_id": pid, "ticker": p["ticker"], "strategy": p["strategy"], "label": label,\n'
  '                   "heads": ("%g× premium" % A.PREMIUM_HEADSUP_X) if hu else None,\n'
  '                   "heads_reading": hu["reading"] if hu else None,\n'),
])

# ---------------- board: templates
MARK = ('{% if r.heads %} <span class="amber" title="{{ r.heads_reading }}">{{ r.heads }}</span> '
        '<a class="q" href="/reference#premium-2x" title="How this is worked out">?</a>{% endif %}')
s = open(PG + "/templates/today.html", encoding="utf-8").read()
old = '{{ r.label }}</a>{{ tv(r.ticker) }}</td>'
assert s.count(old) == 2, "today.html: expected the two position cells, found %d" % s.count(old)
open(PG + "/templates/today.html", "w", encoding="utf-8").write(s.replace(old, '{{ r.label }}</a>{{ tv(r.ticker) }}' + MARK + '</td>'))
print("patched", PG + "/templates/today.html")

Q = ('{% if r.key == \'premium_2x\' %} <a class="q" href="/reference#premium-2x" title="How this is worked out">?</a>{% endif %}')
patch(PG + "/templates/put_card.html", [
 ("  {% set BOOK = ['book_delta'] %}\n",
  "  {% set BOOK = ['book_delta'] %}{% set HEADS = ['premium_2x'] %}\n"),
 ("  {% set own_hot = hot|rejectattr('key', 'in', BOOK)|list %}\n",
  "  {% set own_hot = hot|rejectattr('key', 'in', BOOK + HEADS)|list %}\n"),
 ("{% macro result(r) %}{% if r.key in ('name_limit', 'sector_limit', 'book_delta') %}{{ 'over' if r.state == 'fired' else 'near' }}{% else %}fired{% endif %}{% endmacro %}",
  "{% macro result(r) %}{% if r.key in ('name_limit', 'sector_limit', 'book_delta') %}{{ 'over' if r.state == 'fired' else 'near' }}{% elif r.key == 'premium_2x' %}reached{% else %}fired{% endif %}{% endmacro %}"),
 ("    {% for r in hot %}<tr><td>{{ r.rule }}</td>",
  "    {% for r in hot %}<tr><td>{{ r.rule }}" + Q + "</td>"),
 ("    {% for r in nexts %}<tr><td>{{ r.rule }}</td>",
  "    {% for r in nexts %}<tr><td>{{ r.rule }}" + Q + "</td>"),
])

patch(PG + "/templates/reference.html", [
 ('<dt id="time-21">',
  '<dt id="premium-2x">Buy-back at {{ k.premium_x }} the premium <span class="dim">display only</span></dt><dd>An open put whose '
  'buy-back (the stored mid) is at or above {{ k.premium_x }} the premium taken in, per share: shown amber, "reached", with '
  'the mid and the sale price, on the card and beside the position on Today. Below it, a next line with the buy-back price '
  'at {{ k.premium_x }}. A heads-up, not a rule: it changes no advice, moves nothing into Decisions and closes nothing; '
  'puts have no stop. Added 2026-11-22, from the re-run of the exit replay on real puts.</dd>\n<dt id="time-21">'),
])
