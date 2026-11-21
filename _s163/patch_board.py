# s163: patch a SCRATCH copy of helm-pg. Usage: python3 patch_board.py <helm-pg dir>
import sys, io, os
root = sys.argv[1]
assert "/tmp/" in os.path.abspath(root), "scratch copies only"
def patch(rel, pairs):
    p = os.path.join(root, rel); s = io.open(p, encoding="utf-8").read()
    for old, new in pairs:
        n = s.count(old)
        assert n == 1, "%s: anchor found %d times: %r" % (rel, n, old[:70])
        s = s.replace(old, new)
    io.open(p, "w", encoding="utf-8").write(s)
    print("patched", rel, len(pairs))
patch("templates/today.html", [
('''{% elif pu.top %}
{% macro putrow(c) %}''', '''{% elif pu.top or pu.index %}
{% macro putrow(c) %}'''),
('''  {% if pu.fit %}<tr class="sub"><td colspan="15">Best that fit</td></tr>{% for c in pu.fit %}{{ putrow(c) }}{% endfor %}{% endif %}
''', '''  {% if pu.fit %}<tr class="sub"><td colspan="15">Best that fit</td></tr>{% for c in pu.fit %}{{ putrow(c) }}{% endfor %}{% endif %}{% if not pu.top %}<tr class="sub"><td colspan="15">no put passed every rule on the last run</td></tr>{% endif %}{% if pu.index %}<tr class="sub"><td colspan="15">Index fund <a class="q" href="/reference#index-fund" title="How this is worked out">?</a>{% if pu.index.iv_rank is not none %} · IV rank {{ '%.0f'|format(pu.index.iv_rank) }} · bar {{ '%.0f'|format(pu.index.bar) }}{% endif %}</td></tr>{{ putrow(pu.index) }}{% endif %}
'''),
])
patch("templates/reference.html", [
('''<dt id="fits">Limits: fits ×N, sector / name over, sleeve full</dt>''',
 '''<dt id="index-fund">Index fund</dt><dd>The approved broad index funds are read on every put list whatever their IV rank, under the list's other contract rules (delta {{ k.put_delta }}; {{ k.dte }} days; spread under {{ k.spread_max }} of the mid). The one with the highest yearly return is shown, with its IV rank and the bar ({{ k.ivr_min }}). # is its rank when it also passed every rule, otherwise —. Display only — the list, its ranking and its sizing are unchanged.</dd>
<dt id="fits">Limits: fits ×N, sector / name over, sleeve full</dt>'''),
])
print("done")
