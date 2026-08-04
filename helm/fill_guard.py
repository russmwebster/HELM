"""HELM-152 (W84) -- the fill guard: what was booked must be what was asked for.

Pure and DB-free so it can be tested without a database or a broker.

Why this exists. PG logs a fill by driving `helm open --confirm` over stdin,
answering its prompts in order. When the prompts and the answers get out of
step -- as they did for weeks -- an answer lands on the wrong question. The
CLI then either raises (caught elsewhere) or, worse, receives something
perfectly valid for the prompt it landed on and books it without complaint.

The un-catchable case is a BLANK line: `Prompt.ask(default=...)` treats it as
"accept the default", which is indistinguishable from a deliberate choice. No
exception is raised, no value looks wrong, and the position is booked at a
size nobody chose. GM went into the book at 5 contracts against 10 bought,
and looked entirely normal on every screen until the fills were diffed
against the broker's own statement.

So the guard does not try to detect the drift. It compares the booking about
to be made against what the caller said it wanted, and refuses on any
difference. No threshold to calibrate, and it is blind to the mechanism.
"""

# A fill is money, so compare it as money. Half a cent is below the smallest
# increment anything quotes in; it exists only to absorb float representation.
PRICE_TOL = 0.005


def compare(expected_contracts, expected_fill, actual_contracts, actual_fill,
            price_tol=PRICE_TOL):
    """Return None when the booking matches the expectation, else a reason.

    Either expectation may be None, meaning "not stated" -- that half is not
    checked. Both None means no check at all, which is the interactive CLI
    case: a person watching the prompts is the receipt.
    """
    problems = []
    if expected_contracts is not None:
        try:
            exp_c = int(expected_contracts)
        except (TypeError, ValueError):
            return ("the expected contract count %r is not a whole number"
                    % (expected_contracts,))
        try:
            act_c = int(actual_contracts)
        except (TypeError, ValueError):
            return ("about to book %r contracts, which is not a whole number"
                    % (actual_contracts,))
        if exp_c != act_c:
            problems.append("you asked for %d contract%s, this would book %d"
                            % (exp_c, "" if exp_c == 1 else "s", act_c))
    if expected_fill is not None:
        try:
            exp_f = float(expected_fill)
        except (TypeError, ValueError):
            return "the expected fill %r is not a number" % (expected_fill,)
        try:
            act_f = float(actual_fill)
        except (TypeError, ValueError):
            return "about to book a fill of %r, which is not a number" % (actual_fill,)
        if abs(exp_f - act_f) > price_tol:
            problems.append("you asked for $%.4f, this would book $%.4f"
                            % (exp_f, act_f))
    if not problems:
        return None
    return " and ".join(problems)


def refusal_text(reason):
    """The message a trader sees. Names both numbers; never guesses a cause."""
    return ("This is not what you asked for: %s. Nothing has been recorded. "
            "Check the fill against your broker and log it again." % reason)
