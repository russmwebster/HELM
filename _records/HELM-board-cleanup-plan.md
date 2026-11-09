# HELM — board cleanup plan (s155, 2026-11-08)

*Russ's decision 11-08: the board had become over-busy — numbers wrapped in explanation, rule results written as
HELM's opinion. The **display rules** (standing rules, "Display rules for the board") are the spec. Every board page is
rebuilt to them, one step at a time, each tested and committed on its own.*

## Decisions (Russ, 11-08)
- The 12 display rules (rule 4 amended: when nothing has fired, list the next lines to watch — prices or dates to act on).
- **"Near a limit" = 90% of it**, shown amber — the sleeve, name, sector and delta limits.
- **`advice.json` stays exactly as it is** for now: the Book of Books keeps the engine's sentences. The board's new rule rows are left out of it.
- Put card: the three beliefs, the rule box, "The read", exit tracking since the break, and the cost-to-close history are **cut** from open cards. History (entry vs exit, the buy-back cost line) lives on **closed cards** only.
- Keep vs switch table: four rows, same measure for both paths — average result, middle result, chance of a loss, worst 1 in 10.
- Data health stays off the menu (one line on Today); its page is reached only from that line. "Data health →" leaves Today's footer.
- Off-menu legacy pages (Positions, Scan, Outcomes, Dashboard, Open) are not redesigned — they go to the 12-01 legacy review.

## Mockups (s155, `~/Projects/helm/_s155/mockups/`; numbers from Fri 11-06 15:15)
Put card (CSCO) · Today · Details · Watchlist · PMCC card (CVX, and NVDA with no short) · closed card (XLE).

## Build order
| # | Step | Files |
|---|---|---|
| 1 | Engine: rule rows (rule, result, reading, fired/near/pass/next) from the same comparisons as the advice; days left counted from the reading; closing priced once (mid, ask labelled) | `helm/advice.py`, `helm/decide.py` |
| 2 | Put card (and the shares / covered-call variant) | helm-pg `templates/thesis.html`, `engine_store.py` |
| 3 | PMCC card | `templates/thesis_diag.html`, `engine_store.py` |
| 4 | Today | `templates/today.html`, `engine_store.py` |
| 5 | Details and Watchlist (last / next report, grade date, what's held) | `templates/book.html`, `templates/watchlist.html`, `engine_store.py`, `app.py` |
| 6 | Closed cards | `templates/thesis.html` (closed branch) |
| 7 | Reference page behind the "?" buttons, written from the code | new template, `app.py` |
| 8 | Records: handover, standing rules, ORIENTATION | `_records/` |

Testing every step: `.bak` first; the board started as production starts it on a `VACUUM INTO` copy; old vs new pages for
every open position and some closed ones; every number on a new page checked against the old page or SQL; no table changed;
perturbations caught, control last. `helm restart pg` after each step, outside the snapshot windows.

## Found during the review (to fix in the build)
- CSCO's Today roll box shows a stale close figure ($6.20, −$1,178) from the 11-02 `helm roll` run.
- GOOGL's shares are missing from Today's Holding and Calls sections (they count in the book's delta).
- Days left: some lines count from today, others from the reading (40 vs 42; 12 vs 14).
- Details: the "three diagonals (EQT, IBM, JPM)" note is stale; its intro says Greeks from "11-05 15:15 – 15:15" while the page says readings span 11-05 to 11-06.
- Watchlist: "Next earnings" shows past dates (last reports); DAL has no company name; the Optionable column is all yes.
- Closed XLE card: the beliefs section says "grade FUND — a grade below B makes assignment a problem" (wrong; cut with the beliefs).

## Status
- 11-08: rules and plan agreed; steps 1–2 built (s155).
- 11-08/09: steps 3–8 built, tested and committed (s156) — the PMCC card, Today, Details and the Watchlist, closed puts and covered calls, the Reference page and its "?" links, the records. **The plan is done.**
- Decided in the build: rent cover on the card is `helm pmcc`'s definition; Today, the cards and Details close at the mid (one price); the book's limits at a past close are not shown (not stored); closed cards for retired strategies and paper keep their old layout.
- Fixed from the review list: CSCO's stale roll figure; GOOGL's shares on Today; days left from today everywhere; the stale diagonals note and Details' intro; past "next earnings" dates (now last reports) and DAL's name; the closed XLE card's wrong grade line.
- Left: the covered-call card and a closed PMCC card take the new layouts when one exists; `advice.status()`'s stale "gross" flag.
