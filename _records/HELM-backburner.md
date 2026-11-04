# HELM — back-burner list

*Names Russ wants kept in view that are **not** in HELM's universe. Started 2026-11-03 (s149, Russ's
decision). Kept as a Mac mirror at `~/Projects/helm/_records/HELM-backburner.md`, published to the
project as `claude/HELM-backburner.md`. Edit the Mac copy, publish with `project_write local_path`.*

## What this list is, and isn't
- **Not the universe.** No HELM tool reads this file: `helm puts`, `helm calls`, `helm pmcc` and the
  advice take names from the **active watchlist** only, and offer only grades A–C or approved funds.
  Nothing here is ever offered.
- **Checked only when Russ asks.** The session gives one command, Russ runs it on the Mac, the session
  updates this file and says what moved:
  `cd ~/Projects/helm && mkdir -p _sNNN && helm quality TICKER … --json > _sNNN/backburner-grades-YYYY-MM-DD.json`
  The grader records each result in `ownership_quality`; for names off the watchlist those rows are
  read by nothing.
- **A name leaves the list for the watchlist only by Russ's decision, and only once it grades A–C**
  (the universe rule is unchanged). Before it joins, it needs a **sector and an exposure group** on its
  watchlist row — nothing fills them for a new name, and the 16:15 audit fails without them (XLE, 11-02).
- **Adding a name:** Russ names it; the session grades it as above and adds a row. The Book of Books
  may suggest names; Russ decides.
- Grades are HELM's ownership-quality grade (`helm/ownership.py`): six measures weighted to a score;
  A ≥ 85, B ≥ 70, C ≥ 55, D ≥ 40. A failed hard test (cash burn while unprofitable, a fragile balance
  sheet) caps the score at 32; a warning caps it at 50. N = fewer than 4 of 6 measures available.

## The list
*Grade = latest, with score. "Held back by" = the test or measure that keeps it below C.
Source: the Book's GATE-vs-HELM comparison of 2026-11-03 (GATE's carded data-center names not on HELM's
watchlist; full list `~/Projects/book_of_books/universes/gate-vs-helm-watchlist-2026-11-03.md`).*

| Ticker | Sector (Yahoo) | Grade 11-03 | Held back by | Before | HELM real trades |
|---|---|---|---|---|---|
| CEG | Utilities | **D 50.0** (74.3 uncapped = B) | cash-quality warning cap — see note | D 09-25 | CSP +$412 (06-02 → 06-25) |
| GNRC | Industrials | **D 54.7** | 0.3 short of C: growth 15, returns on capital 31 | D 09-25 | — |
| EQIX | Real Estate | D 44.7 | returns on capital 25, cash quality 5 (REIT: grader not built for it) | D 09-25 | — |
| TLN | Utilities | F 32.0 | balance-sheet gate failed | — | — |
| BE | Industrials | F 32.0 | balance-sheet gate failed | F 09-25 | CSP +$1,803 (06-02 → 06-22) |
| IREN | Financial Services* | F 32.0 | balance-sheet gate failed (latest year a loss) | — | CSP −$6,064 (05-29 → 07-13) |
| NBIS | Communication Services | F 28.1 | balance-sheet gate failed | — | — |
| OKLO | Utilities | F 27.7 | cash-quality gate failed (burn while unprofitable) | — | CSP −$5,488 (05-29 → 07-13) |
| FLNC | Industrials | F 25.8 | cash-quality gate failed | — | — |
| CORZ | Technology | F 25.3 | cash-quality and balance-sheet gates failed | — | — |
| KEEL | Technology | F 24.2 | cash-quality and balance-sheet gates failed | — | — |
| WULF | Financial Services* | F 20.4 | balance-sheet gate failed (latest year a loss) | — | — |
| INV | Financial Services | F 19.2 | cash-quality and balance-sheet gates failed | — | — |
| CIFR | Technology | F 18.6 | cash-quality and balance-sheet gates failed | — | — (Russ holds shares outside HELM) |
| CRWV | Technology | F 13.9 | cash-quality and balance-sheet gates failed | — | LONG_CALL +$3,051 (05-26 → 06-01) |
| FRMI | Real Estate | N 28.5 | can't grade: 3 of 6 measures | — | — |

\* Graded by the **bank method**: Yahoo files these miners under Financial Services with no operating
income, which sends them down the banks' path. They fail either way (latest year a loss), but a miner
should never be graded as a bank — a grader weakness noted 11-03, not changed.

Grades from `_s149/gate-carded-grades-2026-11-03.json` (Russ's run, 2026-11-03 ~09:40).

## Notes
- **CEG (11-03):** free cash flow negative in 3 of 4 years (2022 −$4.0B, 2023 −$7.7B, 2024 −$5.0B,
  2025 +$1.3B) triggers the cash-quality warning, which caps the score at 50. The cause is presentation:
  CEG sold customer receivables to a bank facility, and the cash collected on them was reported as
  "collection of DPP" under **investing** activities (2022 $4,964M, 2023 $7,340M, 2024 $10,217M; none
  in 2025). Counted back, free cash flow was +$0.9B, −$0.4B, +$5.2B, +$1.3B — one negative year, no
  warning, score 74.3 (B). Sources: CEG's 8-K earnings releases for Q4 2022, Q4 2023 and Q4 2025.
  **Russ's decision 11-03: leave it D on this list** (no manual grade, no grader change). On the grader
  as it stands, the cap lasts until the FY2027 report (early 2028) drops 2024 out of its four-year
  window. Not checked: CEG's balance sheet after the Calpine purchase.
- **GNRC:** a rule-is-a-rule near miss; look again at the next check.
- **IREN and OKLO** were two of the May–June put losses that made the grade HELM's main risk control.

## Check log
- **2026-11-03 (s149):** list started with the 16 names above; all graded. None A–C.
