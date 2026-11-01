# HELM — using the Greeks, trade by trade and for the book (plan, s145, 2026-11-01)

*Russ asked (11-01): are we using the Greeks a trader would use to watch the book every day? And by "book" he means
**each trade first, and the whole book too**. This is the plan. **Nothing is built.** Each phase is explained and
waits for Russ's go-ahead; anything that changes advice or an exit is a rulebook decision for him, not a build choice.
Mirror: `~/Projects/helm/_records/HELM-greeks-plan.md` → project `claude/HELM-greeks-plan.md`.*

## Where we are (checked 11-01)

- **The readings are there.** Every open real leg is read at 10:00, 12:30 and 15:15 from IBKR (`greeks_source`
  ibkr-live) with delta, gamma, theta, vega and its implied vol on every row (`leg_checks`: AMZN, CSCO, GOOGL, VST
  14 of 14 since 10-26; DELL 3 of 3). **NVDA's PMCC and XLE have none yet** — first readings Mon 11-02.
  `entry_snapshots` holds the same Greeks and IV at entry (plus `extrinsic_ratio`, `leaps_delta`), so "now vs entry"
  is a read, not a new collection.
- **Used today, per trade:** delta only — the strike band at entry (0.25–0.30) and "tested" (delta ≥ 0.50 inside
  21 days, or the strike crossed). Gamma, theta and vega of a single trade are stored but not shown or used.
- **Used today, whole book:** beta-weighted delta with its limit (half the sleeve), theta against 0.05–0.12% of the
  sleeve, vega summed, the −5%/−10%/+10 vol what-ifs, the market line and the −10…+10% risk curve (s144).
- **Missing, by trader practice:** each trade's own Greeks in dollars; early-assignment risk (time value left) on
  in-the-money shorts; gamma where it is concentrated (one name, one expiry); vega against what IV actually did;
  and the standard daily check — **yesterday's P&L against what the Greeks said it should be**.

Evidence tier: everything below is **trader convention (B)**, not something HELM's own trades have proved.
The thresholds are starting points for Russ to set.

## The principle

The engine computes every number once (`helm/bookview.py`, or a new `helm/greekview.py` it calls); `helm book`,
`helm advise`, Today and Details only show it. **Display first, rules later:** Phases 1, 3 and 4 only show numbers.
Phase 2 adds heads-ups to the advice, and each one waits for Russ to set its line.

## Phase 1 — each trade's Greeks, in dollars (display only)

One Greeks block per open position on Details (and a one-line version in `helm advise`), from its newest reading,
with entry beside it:

| What | Shown as | Why a trader watches it |
|---|---|---|
| **Delta** | shares-equivalent, $ of stock, $ of S&P (beta-weighted); entry → now | how far the trade has moved toward the money; a put's delta drifting up is the early warning before "tested" |
| **Gamma** | "a 1% move in the stock changes this trade's delta by N shares and its P&L by about $X" | which trades will swing hardest — rises fast inside 21 days and near the strike |
| **Theta** | $ a day; **what's left to earn**: the mark ÷ collateral, a year rate over the days left | whether the trade is still worth the cash it ties up (the reason behind the 50% rule) |
| **Vega** | $ per vol point; the stock's IV at entry → now | why a profit-take is slow or fast; a short is hurt by IV rising |
| **Time value left** | mark − intrinsic, $ a share and in total (short legs) | early-assignment risk on an in-the-money short (GOOGL now) |

**PMCC extras** (both legs, then netted): the long's delta against its 0.75–0.85 band; the share of the long's
price that is time value (the rule says "mostly intrinsic"); **net theta** — is the short earning more a day than the
long is losing? (positive = the rent covers the long's decay); net vega (a PMCC is usually long vega).

**Covered calls** (when GOOGL is assigned): the same block, plus the shares' delta (100 per 100) netted with the call.

Build: a per-position function returning these numbers; Details shows a table, `helm book` prints it. No change to
advice verbs. Size: small — every input is already stored. **`advice.json`:** leave unchanged in this phase
(adding fields bumps `schema_version` and is told to the Book) — Russ decides whether the Book gets them.

## Phase 2 — heads-ups in the daily advice (each needs Russ's line first)

Added to `helm/advice.py` as marks on the holding line or "Needs you" items; never an instruction, always the
reason and the number.

1. **Early assignment likely** — an in-the-money short whose time value is below a line (starting suggestion:
   under $0.10 a share, or under 1% of the mark — **Russ sets it**). Shows the assignment in shares and dollars and
   HELM's existing lean (take the shares while A–C). Covers puts, covered calls and the PMCC short.
2. **Ex-dividend on a covered call or PMCC short** — in the money and time value below the coming dividend before
   the ex-date (rulebook §5 already asks for this). **Blocked on a source of ex-dividend dates** (not stored today;
   Yahoo is unreachable from the device VM — `helm calls` reads it on the Mac at run time). Decide the source first.
3. **PMCC long drifting** — the long's delta below 0.75, or time value above a set share of its price → "review the
   long" (today only the 90-days-left review and the loss limit watch it). **Russ sets the line.**
4. **Not worth the cash any more** (optional, a rulebook question) — a short whose remaining yield on collateral
   has fallen below what today's put list offers → a heads-up "closing early frees $X for a better trade." This
   changes exit thinking (it is a softer 50% rule), so it is **Russ's decision whether to have it at all**.

Not proposed: a per-trade gamma or vega rule. The 30–45 day entry, the 21-day exit and IV rank ≥ 50 already do that
job (standing rules, s137).

## Phase 3 — the book, by name and by expiry (display only)

- **Delta by name** — a bar per company (GOOG + GOOGL as one), beta-weighted, against the book's limit: shows when
  one name carries the book's delta (GOOGL did on 10-29). Already on the open list.
- **Gamma by expiry** — how much of the book's gamma sits in each expiry (the 11/20 cluster now): where the book's
  delta will jump fastest as the date nears.
- **Vega in context** — one line: "IV across the book moved +X points since yesterday; vega says that cost about $Y."
  From each position's own IV change × its vega.
- **Theta against delta** — the s144 break-even fall, kept daily so its trend can be read later (store nothing new;
  recompute from readings).

## Phase 4 — yesterday, explained (after a few weeks of clean readings; earliest about 11-23)

The standard daily performance check, per trade and summed for the book:

`actual P&L ≈ delta × stock move + ½ × gamma × move² + theta × days + vega × IV change + rest`

- From the 15:15 reading to the next 15:15 reading, each leg's own Greeks and its own stock's move (not the S&P).
- Shown as one line on Today ("Yesterday +$410: time +$165, the stocks −$120, volatility +$300, rest +$65") and a
  table per trade on Details. A large "rest" on one trade means its Greeks are not describing it (earnings, a quote
  problem, a missed reading) — it names the trade.
- Needs every leg read at both ends: days with a missed snapshot are shown as "can't explain — reading missing",
  never guessed. Split around earnings days.
- Verify on the 10-21 → 10-30 days already studied (`_s144/delta_series.py`): the explained parts must add up to
  the actual change within a stated tolerance, and GOOGL's 10-29 earnings day must show as a large "rest".

## Phase 5 — review (about two months of readings, early January)

With Phase 4's record: is half the sleeve the right delta line; is 0.05–0.12% the right theta range; did the
Phase 2 lines fire usefully or noisily. Russ decides; a standing-rules line only if something changes.

## How each phase is verified (standing rules)

`.bak-YYYYMMDD-sNNN` before editing; a `VACUUM INTO` copy in `/tmp` with `HELM_DB=` pointed at it; IBKR blocked;
every table compared before and after (display phases must change nothing); each number checked by hand on one
real position (AMZN put, NVDA PMCC); a perturbation that must fail (run with `python3 -B`); the board started as
production starts it, every page 200; `helm advise` output compared to before for any position not meant to change.
Engine changes need `helm restart pg`, outside the snapshot windows.

## Decisions Russ owes before Phase 2

1. The early-assignment line (time value a share, or a share of the mark).
2. Where ex-dividend dates come from.
3. The PMCC long's review lines (delta floor; time-value share).
4. Whether "not worth the cash any more" exists at all.
5. Whether the Book of Books' `advice.json` carries the per-trade Greeks (a schema bump).

## Order and size

Phase 1 (one session) → Phase 3 (small, same session or next) → Phase 2, item by item as Russ sets each line →
Phase 4 once readings allow → Phase 5 in January.
