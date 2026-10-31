#!/usr/bin/env python3
"""HELM s101 - W97: let a PINNED contract be recorded even when the entry
screen would not have proposed it.

Two edits to helm/cli/open_cmd.py:
  1. ~3334  when --strike AND --expiry are both given, call evaluate_contracts
            with enforce_long_gates=False and no top-N truncation, so the pin
            resolver sees W85's annotated list instead of a gated one.
  2. ~1344  stamp origin_screen='MANUAL_PIN' on a pinned booking.

Unpinned bookings are byte-identical. Dry-run by default; --apply writes.
Aborts before touching disk if an anchor is not unique, if pin_strike /
pin_expiry are not in scope, or if the result does not compile.
"""
import argparse, difflib, os, re, shutil, sys, datetime

TARGET = 'helm/cli/open_cmd.py'
MARK = 'W97 (s101)'

OLD1 = re.compile(
    r'^([ \t]*)contracts = evaluate_contracts\(ticker, strategy, config, '
    r'dte_target, top_n\)[ \t]*$', re.M)

NEW1 = (
    '\\1# W97 (s101): a pin is an identity, not a candidate. Give the pin\n'
    '\\1# resolver W85\'s annotated list (enforce=False keeps refused\n'
    '\\1# contracts) and skip top-N, so a contract the screen would not\n'
    '\\1# PROPOSE can still be RECORDED. Unpinned callers are unchanged.\n'
    '\\1_w97_pinned = pin_strike is not None and pin_expiry is not None\n'
    '\\1contracts = evaluate_contracts(\n'
    '\\1    ticker, strategy, config, dte_target,\n'
    '\\1    10000 if _w97_pinned else top_n,\n'
    '\\1    enforce_long_gates=not _w97_pinned)')

OLD2 = ('            contracts=num_contracts,\n'
        '            scan_data=scan_data,\n'
        '        )')

NEW2 = ('            contracts=num_contracts,\n'
        '            scan_data=scan_data,\n'
        '            # W97 (s101): a pinned booking is not a screen\n'
        '            # recommendation. W19 grades which screens produce good\n'
        '            # outcomes, so do not credit one with a trade it never\n'
        '            # proposed.\n'
        '            origin_screen=(\n'
        '                "MANUAL_PIN"\n'
        '                if (pin_strike is not None and pin_expiry is not None)\n'
        '                else None\n'
        '            ),\n'
        '        )')


def die(m):
    print('  ABORT: ' + m)
    sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--root', default=os.path.expanduser('~/Projects/helm'))
    a = ap.parse_args()
    path = os.path.join(a.root, TARGET)
    print('HELM s101 - W97 pin bypasses the entry gates')
    print('file: %s' % path)
    print('mode: %s\n' % ('APPLY' if a.apply else 'DRY RUN'))
    if not os.path.exists(path):
        die('no such file')

    src = open(path).read()
    if MARK in src:
        die('already applied')

    n1 = len(OLD1.findall(src))
    n2 = src.count(OLD2)
    print('== ANCHORS ==')
    print('   edit 1  evaluate_contracts call      matches %d (need 1)' % n1)
    print('   edit 2  open_position_with_snapshot  matches %d (need 1)' % n2)
    if n1 != 1 or n2 != 1:
        die('anchor not unique - nothing written')

    m = OLD1.search(src)
    fn = src[src.rfind('\ndef ', 0, m.start()):m.start()]
    for name in ('pin_strike', 'pin_expiry'):
        if not re.search(r'\b%s\b' % name, fn):
            die('%s not in scope at the call site' % name)
    print('   scope   pin_strike / pin_expiry in scope   OK')

    new = OLD1.sub(NEW1, src, count=1).replace(OLD2, NEW2, 1)
    try:
        compile(new, path, 'exec')
    except SyntaxError as e:
        die('patched source does not compile: %s' % e)
    print('   syntax  patched source compiles           OK')

    print('\n== DIFF ==')
    for l in difflib.unified_diff(src.split('\n'), new.split('\n'),
                                  'before', 'after', n=2, lineterm=''):
        print('   ' + l)

    if not a.apply:
        print('\nDRY RUN - nothing written. Re-run with --apply.')
        return

    bak = '%s.bak-s101-w97-%s' % (
        path, datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))
    shutil.copy2(path, bak)
    open(path, 'w').write(new)
    back = open(path).read()
    if back != new:
        shutil.copy2(bak, path)
        die('readback differs - restored from backup')
    compile(back, path, 'exec')
    print('\n   backup:   %s' % bak)
    print('   readback: identical, compiles from disk')
    print('\nAPPLIED. Now run:  helm restart && helm restart pg')


if __name__ == '__main__':
    main()
