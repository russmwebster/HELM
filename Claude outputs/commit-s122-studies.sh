# s122 (2026-09-26/27): the strategy deep-dive and long-call picking-test scripts. ONE commit, no restart.
# Analysis scripts only -- nothing in helm/ or helm-pg/ changed. Price data (_s122/px/) is NOT committed.
cd ~/Projects/helm || exit 1
rm -f .git/index.lock
D="Claude outputs/csp-deep-dive-2026-09-26"
git add \
  "$D/build.py" "$D/factors.py" "$D/replay.py" \
  "$D/ic_build.py" "$D/ic_factors.py" "$D/ic_replay.py" \
  "$D/lc_build.py" "$D/lc_entry.py" "$D/lc_factors.py" "$D/lc_replay.py" \
  "$D/w16_split.py" "$D/w16_plain.py" "$D/w16_factors.py" \
  "_s122/fetch_px.py" "_s122/lc_backtest.py" \
  "Claude outputs/commit-s122-studies.sh"
git commit -F - <<'MSG'
s122: strategy deep-dive and long-call picking-test scripts (read-only studies)

Scripts behind five project docs (HELM-CSP-deep-dive-2026-09-26,
HELM-condor-spread-deep-dive-2026-09-26, HELM-long-call-deep-dive-2026-09-26,
HELM-long-call-picking-comparison-2026-09-27, HELM-long-call-picking-test-2026-09-27)
and the W16 re-run splits. All open helm.db read-only (or a copy of it).

Claude outputs/csp-deep-dive-2026-09-26/
  build/factors/replay      CSPs: dataset, entry factors, exit-rule replays on
                            GOOD marks (paths trimmed to closed_at -- 15 paper
                            CSPs carry checks rows after close, 06-29..07-21)
  ic_*                      iron condors and bear call spreads, same method
  lc_*                      long calls: forward stock returns vs S&P, entry
                            factors, give-back/stop/target/time-stop replays
  w16_*                     W16 re-run splits (old/new sample, June excluded,
                            plain counts, last-week factors) on top of
                            tools/w16_dte_sweep.py (self-test PASS, 110 keys)
_s122/fetch_px.py           10y daily closes/volume for the watchlist, SPY and
                            11 sector ETFs via yfinance -> _s122/px/ (run on
                            the Mac; Yahoo is blocked from the session VMs)
_s122/lc_backtest.py        9-year test of long-call picking rules (current
                            screen, proposal, breakout, pullback, baseline);
                            bias replica agrees with HELM on 81% of scan rows

Findings and Russ's decisions (W16 closed; W160 $5,000 per trade; W189-W199)
are in claude/HELM-archive-2026-09-27.md. Price data in _s122/px/ is not
committed.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Ct3sgkUP38XfQFmnm8MuNw
MSG
git status --porcelain -- "Claude outputs/csp-deep-dive-2026-09-26" _s122/fetch_px.py _s122/lc_backtest.py
echo "Done. Nothing to restart. (Empty status lines above = all committed.)"
