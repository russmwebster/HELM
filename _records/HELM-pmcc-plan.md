# HELM — finding, re-selling and rolling PMCCs (plan, s145, 2026-11-01)

*Russ asked (11-01) whether PMCC screening and recommendations — new entries, selling the next short on a long,
and rolling shorts — need a plan. They do: the PMCC screen on disk is the old diagonal code and can't be reused.
**Russ approved the session's lean on the universe and trend, and settled the step decisions, on 11-01 (below). Nothing is built.** Each step is
explained and waits for Russ's go-ahead; live chains need market hours to test.
Mirror: `~/Projects/helm/_records/HELM-pmcc-plan.md` → project `claude/HELM-pmcc-plan.md`.*

## Where we are (checked 11-01)

- **Booking works.** The Fidelity import (`helm/fillopens.py`) books a new PMCC (a call bought 270–730 days out,
  with or without a same-day short) and the PMCC's next short (a call sold on a name whose PMCC has no short open,
  via `resell_cmd.record`), with the width test, grade and limits as **flags, not refusals**.
- **Advice works.** `helm/advice.py` gives a PMCC one line a day: loss limit (whole trade ≤ −half the long's
  cost) → short in the money near expiry (decide) → short at 50% (resting order) → short at 21 days (close or roll)
  → long at 90 days left (review) → no short (sell the next call; names the width-test floor) → hold.
- **Finding is not built.** `helm puts` / `helm calls` never offer a PMCC. The old screen
  (`helm/cli/diagonal.py` `PMCC_CONFIG` → `open_cmd.evaluate_diagonals`) **reads Yahoo, not IBKR**, uses the **old
  bands** (long 150–730 days, 0.70–0.90 Δ; short 21–45 days, 0.20–0.35 Δ — the rulebook says long 270–730 /
  0.75–0.85, short 30–45 / 0.20–0.30) and gives no reasons for what it leaves out. It is replaced, not adjusted —
  as `helm puts` replaced the old put scan. It is parked with the old scan's routes on 12-01.
- **One PMCC open:** NVDA (long 160 12/17/2027 @ $62.90, short 225 12/04 @ $3.40; width $65 vs $62.90). Its short
  reaches 21 days about **11-13**; NVDA reports **11-17**, inside the short.

## Russ's decisions (11-01) — the session's lean, approved

- **Universe: grade A–C or an approved fund is the floor for PMCC entries.** For a PMCC the grade is not "would I
  own it if assigned" (a PMCC never puts shares in the account) but "would I hold this business through a bad
  year" — the long is a stand-in for about 80 shares for one to two years. It keeps out the companies most likely to
  fall hard and stay down. It is a floor, not a way to find winners: it does not predict direction.
- **Approved funds are a natural PMCC underlying** (SPY, QQQ, sector funds: no single-company collapse, no
  earnings) — they are offered alongside A–C stocks.
- **Trend is shown and flagged, never a gate.** Each PMCC idea shows the stock against its 200-day average (and its
  50-day), with a flag when it is in a clear downtrend (below the 200-day with the 50-day falling) — so taking one is
  a conscious choice. **Every PMCC records its trend at entry**, so the question can be tested once there is a
  record. (Why not a gate: "gate on facts, display judgments"; HELM's put records argued against a trend gate; and
  nothing in HELM shows trend at entry predicts the fall — the s122 nine-year test withdrew the last screen built
  on that hope. Why show it here when it isn't a gate for puts: a PMCC is a direction bet — HELM's long calls won
  17 of 21 when the stock rose and 0 of 36 when it fell.)
- **The structure carries the risk control:** a deep in-the-money long with little time value, one contract per name,
  the width test on every short (an assignment never locks in a loss), the loss limit at half the long's cost, and
  the sleeve, name and sector limits. These bound what a wrong direction costs.
- **Honest limit:** one PMCC per name means the record will build slowly — these choices rest on trader practice
  (tier B), not on HELM's own trades, and will for a long time.

## Build order (most urgent first)

### Step 1 — the next short on a PMCC long: fold PMCC longs into `helm calls`

A call sold against a long call is the same job as a covered call against shares, and `helm/calltool.py` already
does it: IBKR quotes, a wider net with true reasons, **three choices per holding**, every run recorded
(`call_runs`, `call_candidates`), the 50% buy-back price.

- `calltool.holdings` lists **each open PMCC with no short** as a holding (today it reads only `stock_positions`;
  note the s143 finding that it counts a PMCC short as covering shares of the same stock — fix that in the same
  change).
- For a PMCC holding: **0.20–0.30 Δ** (the PMCC band, not the covered call's 0.20–0.35), 30–45 days, spread < 10%;
  **strike floor = the width test** (long strike + what the long has cost net of rent banked, a share — the same
  `fillopens.width_test`, one function, not a copy); yield measured on the long's cost; **earnings inside the short
  allowed and flagged** (Russ 11-01) — unlike covered calls, which keep earnings out.
- `helm advise`'s "sell the next call" points to `cd ~/Projects/helm && helm calls`; Today's covered-calls section
  shows PMCC holdings too.
- Test on copies with faked chains (NVDA with its short removed; a re-sell after banked rent lowers the floor; a
  strike one cent under the floor is left off with the reason); live chains in market hours.

### Step 2 — rolls: the choices side by side

When the advice says the short is tested (in the money near expiry, or delta ≥ 0.50 inside 21 days), at its 50% take,
or at 21 days, `helm calls --roll NVDA` (name to settle) shows, with the numbers for each:

1. **Roll up and out for a net credit** — buy back the short at its ask, sell a 30–45 day call at the same or a higher
   strike at its bid; only rolls that are a net credit **and** pass the width test at the new strike; up to three.
2. **Buy the short back and leave the long without one** (then step 1 next week).
3. **Close the whole trade** (the long at its bid, the short at its ask).

Every run recorded. Quotes at the bid/ask, not the mid, for the credit test (the GOOGL check of 10-30 showed a
roll that is a credit at the mid is a debit at the bid/ask).
**Also test the booking of a same-day PMCC roll through the import** (close + new sale the same day → linked to
the PMCC as its next short) — untested today.
**Tested puts too (Russ 11-01):** the same roll engine prices rolls for tested CSPs — out (and down) for a net credit
at the bid/ask, against taking the shares (HELM's lean while A–C) — GOOGL showed the need.

### Step 3 — new PMCC entries: `helm pmcc`

A command Russ runs, like `helm puts` (not scheduled; refused in the snapshot windows unless `--force`), on the
`helm/puttool.py` pattern:

- **Universe:** A–C or FUND; not a name with a PMCC already open (one per name); a name whose sleeve/name/sector room
  can't take the long → shown as "would be declined" with the limit named.
- **Long:** 270–730 days, 0.75–0.85 Δ, spread < 10%. **Its time value is judged as rent cover, not as a share of
  its price** (Russ 11-01): the long's time value ÷ the days to its expiry × 30 is its monthly cost; the short's credit
  ÷ its days × 30 is the monthly rent; **rent cover = rent ÷ cost, flagged below 2×, never a gate.** (A share-of-price
  line punishes volatile names and long expiries: NVDA's long on 10-30 was 29% time value — $18.26 of $62.90 with the
  stock at $204.64 — yet passed the width test; its rent cover is about 2.2×: $1.33 a month of decay against $2.90 of
  rent.) The width test stays the hard rule.
- **Short:** 30–45 days, 0.20–0.30 Δ, spread < 10%, **width test passes at entry**.
- **Shown, not gates:** trend (200-day and 50-day, the downtrend flag), **IV rank (no rule — Russ 11-01: a PMCC is
  net long volatility, so dear options make the long dearer; that shows up as lower rent cover, which the flag
  catches)**, earnings inside the short (flagged).
- **Ranked** — proposed: by rent cover (the number that says whether a flat stock still pays); to confirm at step 3.
  Top 5, one per name.
- **Recorded:** every name's best pair and each left-off name's true reason; the trend, IV rank, both legs' Greeks.
- **Quotes from IBKR only**, a wider net than the rules, the rules applied by the tool (standing rule: a screen
  casts a wider net than its rules).
- **Trend from IBKR daily bars** (Russ 11-01, after a check): the 200-day simple average and the 50-day's slope from
  a one-year request of daily bars (`reqHistoricalData`, '1 Y', '1 day' — the path `helm/cli/ivr_cmd.py` already uses
  for vol rank). Checked 11-01: IBKR returned NVDA's daily bars 10-20-2025 → 10-30-2026. The old "IBKR gives at most
  120 days" note was wrong for daily bars. Not the old scan's `signals` table (last written 10-23).

## Decisions (Russ, 11-01)

1. **Earnings inside a PMCC short:** allowed, flagged.
2. **Rolls cover tested puts too.**
3. **The long's time value:** judged as rent cover (monthly rent ÷ the long's monthly decay), flagged below 2×; no
   share-of-price line; the width test stays the hard rule. The Greeks plan's "PMCC long drifting" heads-up uses the
   same measure (with the long's delta below 0.75).
4. **IV rank at entry:** no rule; shown.
5. **Trend source:** IBKR daily bars (a year is available); Yahoo not needed.

**Still open:** the ranking for new entries (proposed: rent cover) — confirm at step 3.

## How each step is verified (standing rules)

`.bak-YYYYMMDD-sNNN` before editing; a `VACUUM INTO` copy in `/tmp` with `HELM_DB=`; IBKR blocked or faked chains for
the logic, live chains in market hours for the reading; every table compared before and after (a screen writes only
its run tables); one function for the width test (no second copy); a perturbation that must fail, run with
`python3 -B`; `helm calls` output unchanged for share holdings; the board started as production starts it.
Engine changes need `helm restart pg`, outside the snapshot windows.

## Relation to the Greeks plan

`claude/HELM-greeks-plan.md` Phase 1 shows the PMCC's long delta against its band, the long's time-value share and
net theta; Phase 2's "PMCC long drifting" heads-up uses the same time-value line as Step 3 here — set it once.
