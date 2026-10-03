"""Per-leg entry for credit verticals and iron condors (s126, Russ 2026-10-03).

Why this exists. The condor and credit-spread bookers asked for ONE number,
the net credit, and took the strikes and expiry from HELM's own pick. The
per-leg prices were manufactured from the model's quotes so they summed to
the net (open_cmd scaled the shorts), and a structure placed at the broker
with different strikes or a different expiry could not be recorded at all.
GS (2026-09-14: puts moved at order entry, call legs priced 8.18/6.30 against
fills of 7.35/5.52) and NOW (2026-09-24: Oct 23 117/122/165/170 @1.25 booked,
Oct 30 115/120/170/175 @1.53 filled) are the two real cases. Every single-leg
and diagonal booking in the same window matched its fills to the cent --
those are entered leg by leg.

So these structures are now entered as legs: strike and fill for each, one
expiry. The net credit is DERIVED from the legs, never typed and spread.

Pure and DB-free: parsing, validation and arithmetic only. `--selftest` runs
the frozen cases.

Spec format (named, for non-interactive callers such as the PG board):
    "115P@2.01,120P@3.02,170C@2.33,175C@1.81"   (commas or spaces)
Direction comes from the structure, never from the caller: in a put spread
the higher strike is SHORT, in a call spread the lower strike is SHORT.
"""
import re

# Structure order: the order legs are prompted, shown and stored.
STRUCTURES = {
    "IRON_CONDOR":      (("SHORT", "PUT"), ("LONG", "PUT"),
                         ("SHORT", "CALL"), ("LONG", "CALL")),
    "BULL_PUT_SPREAD":  (("SHORT", "PUT"), ("LONG", "PUT")),
    "BEAR_CALL_SPREAD": (("SHORT", "CALL"), ("LONG", "CALL")),
}

_TOKEN = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([PC])\s*@\s*(\d+(?:\.\d+)?)\s*$", re.I)


def supported(strategy):
    return strategy in STRUCTURES


def parse_spec(strategy, spec):
    """Return legs [{direction, opt_type, strike, fill}] in structure order.
    Raises ValueError with a readable reason; never guesses."""
    if strategy not in STRUCTURES:
        raise ValueError("per-leg entry is not defined for %s" % strategy)
    toks = [t for t in re.split(r"[,\s]+", (spec or "").strip()) if t]
    puts, calls = [], []
    for t in toks:
        m = _TOKEN.match(t)
        if not m:
            raise ValueError("cannot read leg %r -- write it as STRIKE[P|C]@FILL, "
                             "e.g. 120P@3.02" % t)
        k, cp, px = float(m.group(1)), m.group(2).upper(), float(m.group(3))
        (puts if cp == "P" else calls).append((k, px))
    want = STRUCTURES[strategy]
    need_p = sum(1 for _, ot in want if ot == "PUT")
    need_c = len(want) - need_p
    if len(puts) != need_p or len(calls) != need_c:
        raise ValueError("%s needs %d put leg(s) and %d call leg(s); got %d and %d"
                         % (strategy, need_p, need_c, len(puts), len(calls)))
    puts.sort()
    calls.sort()
    by = {}
    if puts:
        by[("LONG", "PUT")], by[("SHORT", "PUT")] = puts[0], puts[1]
    if calls:
        by[("SHORT", "CALL")], by[("LONG", "CALL")] = calls[0], calls[1]
    return [{"direction": d, "opt_type": o, "strike": by[(d, o)][0],
             "fill": by[(d, o)][1]} for d, o in want]


def _get(legs, d, o):
    for l in legs:
        if l["direction"] == d and l["opt_type"] == o:
            return l
    return None


def net_credit(legs):
    """Per share: shorts received minus longs paid, from the legs themselves."""
    return round(sum(l["fill"] if l["direction"] == "SHORT" else -l["fill"]
                     for l in legs), 4)


def width(legs):
    w = 0.0
    sp, lp = _get(legs, "SHORT", "PUT"), _get(legs, "LONG", "PUT")
    sc, lc = _get(legs, "SHORT", "CALL"), _get(legs, "LONG", "CALL")
    if sp and lp:
        w = max(w, sp["strike"] - lp["strike"])
    if sc and lc:
        w = max(w, lc["strike"] - sc["strike"])
    return round(w, 4)


def validate(strategy, legs):
    """None when the legs make a valid credit structure, else the reason."""
    if strategy not in STRUCTURES:
        return "per-leg entry is not defined for %s" % strategy
    if len(legs) != len(STRUCTURES[strategy]):
        return "wrong number of legs"
    for l in legs:
        if l["strike"] is None or l["strike"] <= 0:
            return "a strike must be a positive number"
        if l["fill"] is None or l["fill"] < 0:
            return "a fill cannot be negative"
    sp, lp = _get(legs, "SHORT", "PUT"), _get(legs, "LONG", "PUT")
    sc, lc = _get(legs, "SHORT", "CALL"), _get(legs, "LONG", "CALL")
    if sp and lp and not lp["strike"] < sp["strike"]:
        return "the long put must be below the short put"
    if sc and lc and not sc["strike"] < lc["strike"]:
        return "the long call must be above the short call"
    if sp and sc and not sp["strike"] < sc["strike"]:
        return "the short put must be below the short call"
    nc = net_credit(legs)
    if nc <= 0:
        return ("these fills net a debit of $%.2f -- a credit structure must "
                "collect a credit" % -nc)
    if nc >= width(legs):
        return ("a net credit of $%.2f is not below the width $%.2f -- check "
                "the fills" % (nc, width(legs)))
    return None


def position_fields(legs, contracts):
    """The credit-derived position fields, all from the legs."""
    nc = net_credit(legs)
    w = width(legs)
    pf = {
        "spread_width": w,
        "max_profit": round(nc * 100 * contracts, 2),
        "max_loss": round((w - nc) * 100 * contracts, 2),
        "credit_to_width_ratio": round(nc / w, 4) if w else None,
    }
    sp, sc = _get(legs, "SHORT", "PUT"), _get(legs, "SHORT", "CALL")
    if sp:
        pf["breakeven_low"] = round(sp["strike"] - nc, 2)
    if sc:
        pf["breakeven_high"] = round(sc["strike"] + nc, 2)
    return pf


def label(leg):
    return "%s %s" % (leg["direction"].title(), leg["opt_type"].lower())


def describe(legs, expiry=None):
    """One line: 'Short put 120 @3.02 · Long put 115 @2.01 · ... (Oct 30)'."""
    s = " · ".join("%s %g @%.2f" % (label(l), l["strike"], l["fill"]) for l in legs)
    return s + (" (%s)" % expiry if expiry else "")


def spec_of(legs):
    """The inverse of parse_spec, for callers that build a spec from fields."""
    return ",".join("%g%s@%.2f" % (l["strike"], l["opt_type"][0], l["fill"])
                    for l in legs)


def _selftest():
    ok = 0
    L = parse_spec("IRON_CONDOR", "175C@1.81, 115P@2.01 120P@3.02,170C@2.33")
    assert [(l["direction"], l["opt_type"], l["strike"], l["fill"]) for l in L] == [
        ("SHORT", "PUT", 120.0, 3.02), ("LONG", "PUT", 115.0, 2.01),
        ("SHORT", "CALL", 170.0, 2.33), ("LONG", "CALL", 175.0, 1.81)]; ok += 1
    assert abs(net_credit(L) - 1.53) < 1e-9; ok += 1
    assert validate("IRON_CONDOR", L) is None; ok += 1
    pf = position_fields(L, 3)
    assert pf == {"spread_width": 5.0, "max_profit": 459.0, "max_loss": 1041.0,
                  "credit_to_width_ratio": 0.306, "breakeven_low": 118.47,
                  "breakeven_high": 171.53}, pf; ok += 1
    assert parse_spec("IRON_CONDOR", spec_of(L)) == L; ok += 1
    B = parse_spec("BULL_PUT_SPREAD", "90P@1.10 95P@2.40")
    assert (B[0]["strike"], B[0]["direction"]) == (95.0, "SHORT"); ok += 1
    assert abs(net_credit(B) - 1.30) < 1e-9 and validate("BULL_PUT_SPREAD", B) is None; ok += 1
    C = parse_spec("BEAR_CALL_SPREAD", "210C@0.80,200C@2.10")
    assert (C[0]["strike"], C[0]["direction"]) == (200.0, "SHORT"); ok += 1
    assert position_fields(C, 2)["breakeven_high"] == 201.3; ok += 1
    for strat, spec in (("IRON_CONDOR", "115P@2 120P@3 170C@2"),       # 3 legs
                        ("IRON_CONDOR", "115P@2 120P@3 125P@1 170C@2"),  # 3 puts
                        ("BULL_PUT_SPREAD", "95P@2.40 95C@1"),          # wrong type
                        ("IRON_CONDOR", "120P 115P@2 170C@2 175C@1"),   # no fill
                        ("LONG_CALL", "100C@1")):
        try:
            parse_spec(strat, spec)
            raise AssertionError("accepted %r" % spec)
        except ValueError:
            ok += 1
    D = parse_spec("IRON_CONDOR", "115P@3.02 120P@2.01 170C@1.81 175C@2.33")  # debit
    assert "debit" in validate("IRON_CONDOR", D); ok += 1
    X = parse_spec("IRON_CONDOR", "115P@0 120P@9 170C@0 175C@0")             # credit >= width
    assert "width" in validate("IRON_CONDOR", X); ok += 1
    Y = parse_spec("IRON_CONDOR", "100P@0.5 160P@3 150C@3 175C@0.5")         # crossed shorts
    assert "short put" in validate("IRON_CONDOR", Y); ok += 1
    print("leg_entry selftest: %d/%d PASS" % (ok, ok))


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        _selftest()
