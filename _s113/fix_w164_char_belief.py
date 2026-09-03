import shutil, datetime, py_compile

path = "helm/thesis.py"
ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
backup = "helm/thesis.py.bak-s113-%s" % ts
shutil.copy(path, backup)

with open(path) as f:
    lines = f.readlines()

assert lines[485].startswith("def _char_belief("), lines[485]
assert lines[527].strip().endswith('cur_delta": c_d})'), lines[527]

new_block = '''_LONG_ENTRY_BAND = {
    # s113 (W164) -- the LC/LP entry gate's delta band, from
    # helm/cli/open_cmd.py STRATEGY_CONFIG (delta_min/delta_max). Duplicated
    # here for display, same as _recover_belief's "1.0x the expected move" --
    # thesis.py stays DB/CLI-free by design (see module docstring).
    "LONG_CALL": (0.65, 0.85),
    "LONG_PUT": (0.30, 0.70),
}


def _char_belief(pos, legs, checks, entry_snap):
    """s111 (W162; grading fixed s113 W164) -- position character for the
    long families: the delta you bought vs the delta you hold. Stock-
    replacement is the point of an ITM long-dated call; this grades whether
    the position still IS one. Reads legs.entry_delta / entry_snapshots.delta
    and the journaled check delta; computes nothing new. Display only -- it
    closes nothing.

    W164 (s112): grading the ABSOLUTE current delta let an entry that was
    NEVER stock-replacement (delta below the strategy's entry band) render as
    "eroded" -- false history. Grade the CHANGE from entry delta instead, and
    when entry itself never met the band, say that plainly rather than
    describing a change that never happened."""
    title = "this is stock-like exposure, not a lottery ticket"
    strat = (pos.get("strategy") or "").upper()
    e_d = None
    for l in legs or []:
        if l.get("direction") == "LONG" and l.get("option_type") not in (None, "STOCK"):
            e_d = _f(l.get("entry_delta"))
            break
    if e_d is None:
        e_d = _f((entry_snap or {}).get("delta"))
    c_d = None
    for r in reversed(checks or []):
        c_d = _f(r.get("delta"))
        if c_d is not None:
            break
    then = ("bought %.2f of stock-like exposure" % abs(e_d)) if e_d is not None \\
        else "entry delta not captured"
    if c_d is None:
        return _belief("character", title, then,
                       "no journaled delta yet -- ungraded, not guessed", UNKNOWN,
                       "delta is journaled 3x/day; none on record for this position")
    a = abs(c_d)
    e_a = abs(e_d) if e_d is not None else None
    band = _LONG_ENTRY_BAND.get(strat)
    out_of_band_entry = (band is not None and e_a is not None and
                          not (band[0] <= e_a <= band[1]))

    if out_of_band_entry:
        then = ("bought %.2f -- entered OUTSIDE the %.2f-%.2f stock-"
                 "replacement band" % (e_a, band[0], band[1]))
        now = "holding %.2f" % a
        if a >= 0.90:
            state = HOLDS
            now += " -- mostly stock now despite the out-of-band entry"
        elif a >= band[0]:
            state = FRAYING
            now += (" -- inside the %.2f-%.2f band today, but it didn't enter "
                     "there; call it borrowed character, not held" % band)
        else:
            state = BROKEN
            now += (" -- entered outside the stock-replacement band; this was "
                     "never stock replacement, not something that eroded")
        fine = ("entry delta itself never met the %.2f-%.2f stock-replacement "
                 "band (a pinned entry the screen would have refused -- W97/"
                 "W132) -- so a low current delta reads as an out-of-band "
                 "entry, never as erosion; display only -- it closes nothing"
                 % band)
        return _belief("character", title, then, now, state, fine,
                       {"entry_delta": e_d, "cur_delta": c_d})

    now = "holding %.2f" % a
    if a >= 0.90:
        state = HOLDS
        now += " -- mostly stock now: little convexity left, and the option is doing little the shares would not"
    elif e_a is None:
        if a >= 0.60:
            state = HOLDS
            now += " -- still the position you chose"
        elif a >= 0.50:
            state = FRAYING
            now += " -- halfway to a coin-flip; entry delta wasn't captured, so this reads on level alone"
        else:
            state = BROKEN
            now += " -- deep into coin-flip territory; entry delta wasn't captured, so this reads on level alone"
    else:
        change = a - e_a
        if change <= -0.20:
            state = BROKEN
            now += (" -- down %.2f from the %.2f bought at entry: no longer "
                     "stock replacement" % (-change, e_a))
        elif change <= -0.10:
            state = FRAYING
            now += (" -- down %.2f from the %.2f bought at entry: the "
                     "character you bought is eroding" % (-change, e_a))
        elif change >= 0:
            state = HOLDS
            now += " -- up %.2f from the %.2f bought at entry" % (change, e_a)
        else:
            state = HOLDS
            now += (" -- still close to the %.2f bought at entry (down %.2f)"
                     % (e_a, -change))

    if band is not None:
        fine = ("current delta from the latest journaled check vs the CHANGE "
                 "from the delta bought at entry (bands: -0.10 fraying, -0.20 "
                 "broken); entry sat inside the %.2f-%.2f stock-replacement "
                 "band, so a falling delta here is erosion, not a bad entry; "
                 "display only -- it closes nothing" % band)
    else:
        fine = ("current delta from the latest journaled check vs the CHANGE "
                 "from the delta bought at entry (bands: -0.10 fraying, -0.20 "
                 "broken); display only -- it closes nothing")
    return _belief("character", title, then, now, state, fine,
                   {"entry_delta": e_d, "cur_delta": c_d})
'''

lines[485:528] = [new_block]

with open(path, "w") as f:
    f.writelines(lines)

py_compile.compile(path, doraise=True)
print("OK, backup at", backup)
