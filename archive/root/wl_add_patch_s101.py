#!/usr/bin/env python3
"""s101 -- make `helm watchlist add --evaluate` produce a scannable ticker.

The evaluation already establishes whether a ticker has a real options market,
shows it to you, and asks you to confirm it -- then saves the row WITHOUT
writing that answer. So the row lands active=0 / is_optionable=0, and
`helm scan` (which takes active=1 AND is_optionable=1) never sees it.

Edit 1: write is_optionable and active from the verdict the evaluation produced.
Edit 2: the plain `add` path's tip claims --evaluate gives "optionability
        feedback". True and misleading -- feedback to the user, not the
        database. Say what actually happens.

Dry-run by default; --apply writes. Aborts if an anchor is not unique or the
result does not compile.
"""
import argparse, difflib, os, shutil, sys, datetime

TARGET = 'helm/cli/watchlist.py'
MARK = 's101'

OLD1 = ('            beta=res.get("beta"),\n'
        '        )')
NEW1 = ('            beta=res.get("beta"),\n'
        '            # s101: the evaluation above just established whether this\n'
        '            # ticker has a real options market. Write it down -- without\n'
        '            # these two the row lands active=0 / is_optionable=0 and\n'
        '            # `helm scan` (active=1 AND is_optionable=1) never sees it.\n'
        '            is_optionable=0 if res.get("verdict") == "FLAG" else 1,\n'
        '            active=1,\n'
        '        )')

OLD2 = 'Tip: use [bold]--evaluate[/bold] flag for optionability feedback.'
NEW2 = ('Added, but NOT in scans -- helm scan only sees evaluated tickers. '
        'Re-add with [bold]--evaluate[/bold] to include them.')


def die(m):
    print('  ABORT: ' + m); sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--root', default=os.path.expanduser('~/Projects/helm'))
    a = ap.parse_args()
    path = os.path.join(a.root, TARGET)
    print('HELM s101 -- watchlist add writes what it measured')
    print('file: %s\nmode: %s\n' % (path, 'APPLY' if a.apply else 'DRY RUN'))
    if not os.path.exists(path):
        die('no such file')
    src = open(path).read()
    if MARK in src:
        die('already applied')
    n1, n2 = src.count(OLD1), src.count(OLD2)
    print('== ANCHORS ==')
    print('   edit 1  WatchlistItem.add call   matches %d (need 1)' % n1)
    print('   edit 2  the misleading tip       matches %d (need 1)' % n2)
    if n1 != 1 or n2 != 1:
        die('anchor not unique -- nothing written')
    new = src.replace(OLD1, NEW1, 1).replace(OLD2, NEW2, 1)
    try:
        compile(new, path, 'exec')
    except SyntaxError as e:
        die('patched source does not compile: %s' % e)
    print('   syntax  compiles                                 OK')
    print('\n== DIFF ==')
    for l in difflib.unified_diff(src.split('\n'), new.split('\n'),
                                  'before', 'after', n=2, lineterm=''):
        print('   ' + l)
    if not a.apply:
        print('\nDRY RUN -- nothing written. Re-run with --apply.'); return
    bak = '%s.bak-s101-wl-%s' % (path, datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))
    shutil.copy2(path, bak)
    open(path, 'w').write(new)
    back = open(path).read()
    if back != new:
        shutil.copy2(bak, path); die('readback differs -- restored')
    compile(back, path, 'exec')
    print('\n   backup:   %s' % bak)
    print('   readback: identical, compiles from disk')
    print('\nAPPLIED. Run: helm restart && helm restart pg')


if __name__ == '__main__':
    main()
