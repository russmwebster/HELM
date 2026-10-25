# HELM — the one-page board (plan, decided 2026-10-24, s135)

*Russ's ask: the board is overbuilt and takes too long to work. He wants it to advise
plainly what to do with each trade, on one page. Approved in s135: this layout, the
panel sort below, the rule wording, and the build order.*

**Status 2026-10-25 (s135): built, ahead of the import at Russ's request** — `helm/advice.py` (`helm advise`) and the
board's Today page. Changes from the plan below, by Russ's decisions of 10-25: earnings on puts and diagonals are a
mark on the holding line (highlighted inside 7 days), not a Needs-you item; **every rule overstepped is flagged for
Russ to decide** (sleeve, name, sector, a held name below C, earnings inside a covered call) in its own "Over your
rules" group above Needs you.*

## The rule (in the standing rules)
HELM advises in plain words: each real position gets one recommendation (hold, take the
profit, close or roll, decide on assignment, heads-up), with its reason and the number
behind it. Russ decides and places every trade; HELM never acts. A recommendation
without its reason is a defect. **Computed in the engine, one function; the board only
shows it** (the board holds no model).

## The recommendation verbs (from rulebook v0.2 — re-read the rulebook before building)
- **Hold** — "34% of the profit taken, 26 days left, nothing due."
- **Take the profit** — at 50%: "your resting order at $X should fill; if not placed, buy back at $X."
- **Close or roll — 21 days left.**
- **Tested — roll or take the shares?** Both shown, plus HELM's lean (assignment is the default while the name grades A–C; not for D/F/N — VST and EQT today).
- **Heads-up** — earnings or an ex-dividend date inside the trade.
- **PMCC** — sell the next call (width test answered yes/no) · review the long at 90 days left · loss limit reached.
- **Run-off diagonals (EQT, IBM, JPM)** — their existing rules (loss limit, long's own rules), same one-line form.

## The page, top to bottom
1. **Status line** — sleeve in use vs sleeve and room ("Sleeve full — $302,549 of $300,000; no new puts until 11/20 frees $288,600"); marks' freshness; one word on whether the day collected cleanly (the audit's verdict).
2. **Needs you** — only positions whose answer is not Hold: verb · one-sentence reason · the number · the price to act at. Pending keep/close decisions on earlier flags fold in here. Most days: "Nothing needs you."
3. **New trades** — the ranked list's top 5 puts with resting-order prices (when built); until then one line on whether there is room.
4. **Holding** — one line each: ticker, structure, days left, % of profit taken, P&L $, earnings mark. Click → the position's card.
5. **Footer links** to the detail pages.

## Panel sort (from the s135 inventory of `~/Projects/helm-pg`)
**Folded into the page:** the 50% buy-back prices (Book) → "price to act at"; sleeve room and the 11/20 release (Book) → status line; exit alerts and the pending keep box (Positions) → Needs you; earnings marks; data freshness. The close form stays, reached from the card (needed until the import works).

**One click deeper (kept):** position cards (`thesis.html`, `thesis_diag.html`); the Book page as "Details" (Greeks, what-ifs, name/sector bars, expiries); per-position columns (Δ, θ/ν, buffer %, IV rank, breakeven, price source); Watchlist and grades; Agents (health — the page keeps one word); Open (checking a contract).

**Cut (switched off, not deleted):** REAL/PAPER/ALL toggle and "Generate paper book"; the Scan page (old strategy-bias screen — replaced by the ranked list); Outcomes' paper, family and exit-rule studies (a small real-book net-P&L line can return later); the spread, condor and long-call column sets; the Exposure bar (W135) and the old risk-cap / long-premium panel (W160/W194) — the sleeve replaces both; the four pulse cards (open P&L, capital at work, concentration/drawdown) — the status line replaces them; the paper "rule" pill; the stop-day counter on CSPs (no stop under the rules); the legend box; sandbox/setup pages; the Dashboard nav page (the one page becomes home).

## Build order
1. **The Fidelity import** — fresh session, before IBM's short call expires 10/30.
2. **The recommendation function** in the engine — one module, a `--selftest`; run on today's real positions and shown to Russ as a list before any page work.
3. **The page** — new home route; old pages switched off, not deleted; verified by starting the board the way production starts it, against a database copy.
