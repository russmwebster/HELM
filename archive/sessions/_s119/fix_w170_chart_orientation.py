"""W170 - the close-track chart says which way is better, in words.

Three edits to helm/thesis.py close_svg(), all display, all additive:
  1. an axis-direction caption in the top margin  ("down better - costs less
     to buy back" for a credit, mirrored for a debit)
  2. the day-over-day chip carries the WORD, not just a coloured glyph
  3. the aria-label states the direction too

Nothing about the plotted series, the domain, the trail or the clip bands
changes. The arrow glyphs stay, so tools/verify_s97_h147.py's chip assertion
is untouched.
"""
import py_compile, shutil, sys, time
from pathlib import Path

root = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
src = root / "helm" / "thesis.py"
apply = "--apply" in sys.argv

text = src.read_text()
orig = text

# --- 1. the delta chip gains its word -------------------------------------
OLD_CHIP = '''        _tc = "var(--viz-good,#2a78d6)" if tr["better"] else "var(--viz-bad,#e34948)"
        e.append('<text x="%.1f" y="%.1f" font-size="10.5" font-weight="600" fill="%s">%s %s</text>'
                 % (X(n - 1) + 9, Y(last["value"]) + 17, _tc,
                    "▼" if tr["d1"] < 0 else "▲", _amt(abs(tr["d1"]))))'''

NEW_CHIP = '''        _tc = "var(--viz-good,#2a78d6)" if tr["better"] else "var(--viz-bad,#e34948)"
        # The glyph states the MONEY direction; the word states what that means
        # for this structure. A bare triangle cannot -- a falling buy-back cost
        # is a down arrow and a good day, and colour alone must never carry it
        # (the card's own icon-plus-words rule).
        if track["credit"]:
            _tw = "cheaper" if tr["d1"] < 0 else "dearer"
        else:
            _tw = "fetching less" if tr["d1"] < 0 else "fetching more"
        e.append('<text x="%.1f" y="%.1f" font-size="10.5" font-weight="600" fill="%s">%s %s %s</text>'
                 % (X(n - 1) + 9, Y(last["value"]) + 17, _tc,
                    "▼" if tr["d1"] < 0 else "▲", _amt(abs(tr["d1"])), _tw))'''

if OLD_CHIP not in text:
    sys.exit("REFUSED: chip block not found verbatim -- has close_svg moved?")
text = text.replace(OLD_CHIP, NEW_CHIP, 1)

# --- 2. the axis-direction caption ----------------------------------------
OLD_TICKS = '''    every = max(1, -(-n // 6))'''

NEW_TICKS = '''    # Which way is better, in words, in the top margin. The area fill already
    # says it in colour at 0.15 opacity; a 2px ink line descending says the
    # opposite louder. HELM-146 settled that the NET number leads on the
    # headline -- this is the same settlement applied to the picture.
    _cap = ("↓ better — costs less to buy back than you took in"
            if track["credit"] else
            "↑ better — sells back for more than you paid")
    e.append('<text x="%.1f" y="%.1f" font-size="10.5" fill="var(--viz-muted,#898781)">%s</text>'
             % (ml, mt - 8, _cap))

    every = max(1, -(-n // 6))'''

if OLD_TICKS not in text:
    sys.exit("REFUSED: tick-label block not found verbatim")
text = text.replace(OLD_TICKS, NEW_TICKS, 1)

# --- 3. the aria-label says it too ----------------------------------------
OLD_ARIA = '''    return ('<svg viewBox="0 0 %d %d" role="img" aria-label="What it would cost to close, on each check day" '''
NEW_ARIA = '''    _aria = ("What it would cost to close, on each check day -- lower is better"
             if track["credit"] else
             "What closing would pay, on each check day -- higher is better")
    return ('<svg viewBox="0 0 %d %d" role="img" aria-label="%s" '''

if OLD_ARIA not in text:
    sys.exit("REFUSED: aria-label line not found verbatim")
text = text.replace(OLD_ARIA, NEW_ARIA, 1)

OLD_FMT = '''            'style="display:block;width:100%%;height:auto;overflow:visible">%s</svg>'
            % (width, height, "".join(e)))'''
NEW_FMT = '''            'style="display:block;width:100%%;height:auto;overflow:visible">%s</svg>'
            % (width, height, _aria, "".join(e)))'''
if OLD_FMT not in text:
    sys.exit("REFUSED: svg format tail not found verbatim")
text = text.replace(OLD_FMT, NEW_FMT, 1)

print("edits staged: 4 replacements, %d -> %d bytes" % (len(orig), len(text)))
if not apply:
    print("DRY RUN -- pass --apply to write")
    sys.exit(0)

bak = src.with_name("thesis.py.bak-s119-" + time.strftime("%Y%m%d-%H%M%S"))
shutil.copy2(src, bak)
src.write_text(text)
back = src.read_text()
assert back == text, "readback mismatch"
py_compile.compile(str(src), doraise=True)
print("WROTE %s (backup %s), readback OK, py_compile OK" % (src, bak.name))
