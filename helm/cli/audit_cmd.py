# helm/cli/audit_cmd.py — end-of-day collection audit (W96 Layer 1)
#
# Asserts that the day's data collection actually happened, rather than
# reporting that it looks fine. Every assertion names the query behind it,
# and the report ends with what the audit is BLIND to.
#
# READ-ONLY. Opens the database with mode=ro and writes nothing to it.
# The only file written is the dated report under logs/.
#
# Usage:  helm audit eod [--date YYYY-MM-DD] [--json] [--no-report]
# Exit:   0 = no FAIL   1 = at least one FAIL

import argparse
import csv
import json as _json
import os
import sqlite3
import subprocess
import sys
from collections import OrderedDict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DB = Path(os.environ.get("HELM_DB", ROOT / "data" / "helm.db"))
LOGS = ROOT / "logs"
SAMPLER_CSV = LOGS / "mktdata_samples.csv"

# The roster, per the worklist. Exactly six.
EXPECTED_AGENTS = [
    "com.helm.audit.eod",
    "com.helm.ivr.refresh",
    "com.helm.mktsampler",
    "com.helm.paper.exits",
    "com.helm.pg",
    "com.helm.server",
    "com.helm.snapshot.daily",
]

# Expected scheduled WRITES on a session day: (agent substring, nominal times, label)
EXPECTED_RUNS = [
    ("ivr", ["09:35"], "ivr refresh"),
    ("snapshot", ["10:00", "12:30", "15:15"], "snapshot"),
    ("exits", ["15:35"], "paper exits"),
]

# How far from its nominal time a run may start before it is a catch-up, not a run.
SLOT_TOLERANCE_MIN = 20

# The sampler's steady RTH rate. An hour far below this means the MACHINE was
# down, not that an agent misbehaved. Measured over 2026-07-28..08-12:
# 123-126 samples/day in RTH every day except the 07-29 reboot, which read 12.
MIN_SAMPLES_PER_HOUR = 3

# Legacy / meaningless exit reasons — a close carrying one is not labelled.
UNLABELLED_REASONS = {None, "", "manual"}

PASS, FAIL, WARN, SKIP = "PASS", "FAIL", "WARN", "SKIP"


def _mins(hhmm):
    try:
        return int(hhmm[:2]) * 60 + int(hhmm[3:5])
    except Exception:
        return None


def _within(actual, nominal, tol=SLOT_TOLERANCE_MIN):
    a, n = _mins(actual), _mins(nominal)
    return a is not None and n is not None and abs(a - n) <= tol


class Audit:
    def __init__(self, date_str):
        self.date = date_str
        self.results = []
        self.blind = []
        self.machine_offline_day = False
        self.runs = []
        self.db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        self.db.row_factory = sqlite3.Row

    # ---------- plumbing ----------

    def add(self, status, name, detail, evidence=""):
        self.results.append(
            {"status": status, "name": name, "detail": detail, "evidence": evidence}
        )

    def q(self, sql, args=()):
        return self.db.execute(sql, args).fetchall()

    def blind_spot(self, text):
        self.blind.append(text)

    @property
    def failed(self):
        return any(r["status"] == FAIL for r in self.results)

    # ---------- A1: is this a session day? ----------

    def check_session(self):
        try:
            from helm.market_calendar import session_state

            y, m, d = (int(x) for x in self.date.split("-"))
            st = session_state(datetime(y, m, d, 12, 0))
            self.is_session = bool(st.get("run"))
            reason = st.get("reason", "")
            if st.get("degraded"):
                self.add(
                    WARN,
                    "session day",
                    "calendar answered in FAIL-OPEN mode — treat the verdict as unconfirmed",
                    f"session_state -> {st}",
                )
            elif self.is_session:
                self.add(PASS, "session day", f"{self.date} is an exchange session", reason)
            else:
                self.add(SKIP, "session day", f"{self.date} is NOT a session — {reason}", reason)
        except Exception as e:
            # Fail loudly rather than assuming a weekday is a session.
            self.is_session = datetime.strptime(self.date, "%Y-%m-%d").weekday() < 5
            self.add(
                WARN,
                "session day",
                f"market_calendar unavailable ({type(e).__name__}) — fell back to a WEEKDAY test, "
                "which does not know about holidays",
                str(e)[:120],
            )

    def check_stand_down(self):
        """On a non-session day the three writers must have RECORDED standing down."""
        rows = self.q(
            "select agent, status from agent_runs where date(started_at) = ?", (self.date,)
        )
        skipped = [r for r in rows if (r["status"] or "") == "SKIPPED_CLOSED"]
        if len(skipped) >= 3:
            self.add(
                PASS,
                "stand-down recorded",
                f"{len(skipped)} agents recorded SKIPPED_CLOSED on a non-session day",
            )
        elif not rows:
            self.add(
                FAIL,
                "stand-down recorded",
                "non-session day, and NO agent recorded anything at all — "
                "silence is indistinguishable from the agents being unloaded",
            )
        else:
            self.add(
                FAIL,
                "stand-down recorded",
                f"non-session day: {len(skipped)} of {len(rows)} rows say SKIPPED_CLOSED; "
                f"the rest attempted work against a shut exchange",
                str([dict(r) for r in rows])[:300],
            )

    # ---------- A2: the launchd roster ----------

    def check_roster(self, is_today):
        try:
            out = subprocess.run(
                ["launchctl", "list"], capture_output=True, text=True, timeout=20
            ).stdout
        except Exception as e:
            self.add(WARN, "agent roster", f"could not run launchctl ({type(e).__name__})", str(e)[:120])
            return
        loaded = sorted({
            ln.split("\t")[-1] for ln in out.splitlines() if "com.helm" in ln
        })
        missing = [a for a in EXPECTED_AGENTS if a not in loaded]
        extra = [a for a in loaded if a not in EXPECTED_AGENTS]
        note = "" if is_today else (
            " — NOTE: this reads the roster NOW, not as it was on the audited date"
        )
        if not missing and not extra:
            self.add(PASS, "agent roster", f"exactly {len(loaded)} expected agents loaded{note}")
        else:
            self.add(
                FAIL,
                "agent roster",
                f"missing={missing or 'none'} unexpected={extra or 'none'}{note}",
                str(loaded),
            )
        if not is_today:
            self.blind_spot(
                "The roster check is a NOW check. An agent unloaded on the audited date and "
                "reloaded since would pass here. (W29: launchctl bootout is runtime-only.)"
            )

    # ---------- machine liveness: was the Mac even awake? ----------

    def load_runs(self):
        self.runs = self.q(
            "select agent, slot, status, attempted, journaled, failed, started_at "
            "from agent_runs where date(started_at) = ? order by started_at",
            (self.date,),
        )
        # An agent that RAN in an hour proves the machine was up in that hour,
        # whatever the sampler says. This outranks the sampler as evidence.
        self.run_hours = {
            (r["started_at"] or "")[11:13] for r in self.runs if r["started_at"]
        }

    def machine_liveness(self):
        """Per-hour {hh: n_samples} from the sampler, which ticks continuously.

        The sampler is the independent read on whether the machine was up. It is
        not infallible: on 2026-07-29 it logged 12 RTH samples against a normal
        123-126 because the REBOOT unloaded it, while the machine itself came back
        and other agents ran. So agent_runs evidence overrides it (see load_runs).
        """
        self.samples = {}
        self.machine_known = False
        if not SAMPLER_CSV.exists():
            return
        try:
            with open(SAMPLER_CSV, errors="replace") as fh:
                for row in csv.DictReader(fh):
                    ts = row.get("ts", "")
                    if ts.startswith(self.date):
                        hh = ts[11:13]
                        self.samples[hh] = self.samples.get(hh, 0) + 1
            self.machine_known = True
        except Exception:
            self.machine_known = False

    def machine_up_at(self, hhmm):
        """True/False/None (None = cannot tell) for the hour containing hhmm."""
        if hhmm[:2] in self.run_hours:
            return True  # something ran; the machine was demonstrably up
        if not self.machine_known:
            return None
        return self.samples.get(hhmm[:2], 0) >= MIN_SAMPLES_PER_HOUR

    def check_machine(self):
        if not self.machine_known:
            self.add(WARN, "machine awake", "no sampler data — machine liveness UNKNOWN for this date")
            return
        rth = {h: n for h, n in self.samples.items() if "09" <= h <= "15"}
        total = sum(rth.values())
        dead = sorted(h for h in ("09", "10", "11", "12", "13", "14", "15") if rth.get(h, 0) < MIN_SAMPLES_PER_HOUR)
        ev = " ".join(f"{h}:{rth.get(h, 0)}" for h in sorted(("09", "10", "11", "12", "13", "14", "15")))
        if not dead:
            self.add(PASS, "machine awake", f"sampler ticked through all RTH hours ({total} samples)", ev)
        elif len(dead) >= 6 and not self.run_hours:
            self.machine_offline_day = True
            self.add(
                WARN,
                "machine awake",
                f"the machine was OFF or ASLEEP for essentially the whole session "
                f"({total} RTH samples, and no agent recorded anything). Nothing below is a "
                "HELM defect — the data is simply not there, and there is nothing to fix.",
                ev,
            )
        elif len(dead) >= 6:
            self.add(
                WARN,
                "machine awake",
                f"the SAMPLER was silent for {len(dead)} RTH hour(s) ({total} samples) but other "
                f"agents ran at {sorted(self.run_hours)} — so the machine was up and the "
                "SAMPLER itself was down. Treat the slot verdicts below as authoritative and "
                "the sampler as the thing to fix.",
                ev,
            )
        else:
            self.add(
                WARN,
                "machine awake",
                f"machine down for {len(dead)} RTH hour(s): {', '.join(dead)}. "
                "Readings lost in those hours are an availability gap, not an agent fault.",
                ev,
            )

    # ---------- A3: did every expected slot fire, and on time? ----------

    def check_slots(self):
        rows = self.runs
        for key, times, label in EXPECTED_RUNS:
            got = [r for r in rows if key in (r["agent"] or "").lower()]
            for nominal in times:
                near = [r for r in got if _within((r["started_at"] or "")[11:16], nominal)]
                name = f"slot: {label} {nominal}"
                if near:
                    self.add(PASS, name, f"ran at {(near[0]['started_at'] or '')[11:16]}")
                    continue
                if not self._slot_due(nominal):
                    self.add(SKIP, name,
                             "not due yet — scheduled %s, it is %s"
                             % (nominal, self._now_hhmm()))
                    continue
                up = self.machine_up_at(nominal)
                if up is False:
                    self.add(
                        WARN,
                        name,
                        "no run — and the MACHINE WAS DOWN at that hour. "
                        "Data lost to availability, not to a defect. Nothing to fix in HELM.",
                    )
                elif up is None:
                    self.add(
                        WARN,
                        name,
                        "no run, and machine liveness could not be established — "
                        "cannot tell a sleeping Mac from a broken agent.",
                    )
                else:
                    self.add(
                        FAIL,
                        name,
                        "no run, and the machine WAS up at that hour. "
                        "An agent that dies before record_run leaves silence that reads "
                        "identically to never being scheduled — check its log directly.",
                    )
            # Runs that fired nowhere near a nominal time = launchd catch-up.
            stray = [
                r for r in got
                if not any(_within((r["started_at"] or "")[11:16], t) for t in times)
            ]
            if stray:
                self.add(
                    FAIL,
                    f"slot timing: {label}",
                    f"{len(stray)} run(s) fired more than {SLOT_TOLERANCE_MIN} min from any "
                    "scheduled time. launchd runs a missed job at WAKE, so a slot that was "
                    "asleep can fire hours late and journal quotes stamped with the wrong "
                    "market moment.",
                    str([(r["started_at"] or "")[11:16] for r in stray]),
                )

    # ---------- A4: was the broker actually reachable? ----------

    def check_broker(self):
        """The cause-namer. Downstream emptiness is a symptom; this is the cause."""
        if not SAMPLER_CSV.exists():
            self.add(WARN, "broker reachable", f"sampler csv not found at {SAMPLER_CSV}")
            return
        by_hour = OrderedDict()
        total = conn = 0
        try:
            with open(SAMPLER_CSV, errors="replace") as fh:
                for row in csv.DictReader(fh):
                    ts = row.get("ts", "")
                    if not ts.startswith(self.date):
                        continue
                    hh = ts[11:13]
                    if not ("09" <= hh <= "16"):
                        continue
                    slot = by_hour.setdefault(hh, [0, 0])
                    slot[0] += 1
                    total += 1
                    if row.get("connected") == "1":
                        slot[1] += 1
                        conn += 1
        except Exception as e:
            self.add(WARN, "broker reachable", f"could not parse sampler csv ({type(e).__name__})")
            return

        if total == 0:
            self.add(
                WARN,
                "broker reachable",
                "the sampler recorded NOTHING in RTH — no independent read on the broker. "
                "A zero here is a missing sampler, not a quiet market.",
            )
            return

        dead = [h for h, (n, c) in by_hour.items() if c == 0]
        pct = 100.0 * conn / total
        ev = " ".join(f"{h}:{c}/{n}" for h, (n, c) in by_hour.items())
        if not dead:
            self.add(PASS, "broker reachable", f"{pct:.0f}% of RTH samples connected", ev)
        else:
            self.add(
                FAIL,
                "broker reachable",
                f"IB Gateway unreachable for {len(dead)} RTH hour(s): {', '.join(dead)} "
                f"({pct:.0f}% of samples connected overall). "
                "Readings lost in these windows are a BROKER outage, not an agent defect.",
                ev,
            )

    # ---------- A5: journaled vs attempted ----------

    def check_coverage(self):
        snaps = [r for r in self.runs if "snapshot" in (r["agent"] or "").lower()]
        if not snaps:
            self.add(SKIP, "reading coverage", "no snapshot runs to measure")
            return
        bad = [r for r in snaps if (r["journaled"] or 0) < (r["attempted"] or 0)]
        detail = " ".join(
            f"{(r['started_at'] or '')[11:16]}:{r['journaled']}/{r['attempted']}" for r in snaps
        )
        if not bad:
            self.add(PASS, "reading coverage", "every snapshot journaled everything it attempted", detail)
        else:
            lost = sum((r["attempted"] or 0) - (r["journaled"] or 0) for r in bad)
            self.add(
                FAIL,
                "reading coverage",
                f"{len(bad)} of {len(snaps)} slots dropped readings; {lost} readings lost",
                detail,
            )
        anyfail = [r for r in snaps if (r["failed"] or 0) > 0]
        if anyfail:
            self.add(FAIL, "snapshot errors", f"{len(anyfail)} run(s) recorded failed > 0", detail)
        self.blind_spot(
            "paper.exits is EXCLUDED from this assertion. Its record_run call passes "
            "(evaluated, closed, skipped), so 'attempted > journaled' means it correctly HELD "
            "positions. Its SHORTFALL and EMPTY statuses carry no information. (W102)"
        )

    # ---------- A6: marks on the book ----------

    def check_marks(self):
        n_checks = self.q(
            "select count(*) n, sum(case when data_quality <> 'GOOD' then 1 else 0 end) bad, "
            "sum(case when current_price is null then 1 else 0 end) nomark "
            "from checks where date(checked_at) = ?",
            (self.date,),
        )[0]
        if not n_checks["n"]:
            if self.machine_offline_day:
                self.add(WARN, "marks written",
                         "ZERO check rows — consistent with the machine being off all session")
            else:
                self.add(FAIL, "marks written",
                         "ZERO check rows on the audited date, and the machine was up")
            return
        parts = [f"{n_checks['n']} readings"]
        status = PASS
        if n_checks["bad"]:
            parts.append(f"{n_checks['bad']} not GOOD")
            status = FAIL
        if n_checks["nomark"]:
            parts.append(f"{n_checks['nomark']} with NULL price")
            status = FAIL
        self.add(status, "marks written", ", ".join(parts))

        # Open positions with no reading, not explained by a close.
        unmarked = self.q(
            "select p.id, p.ticker, p.strategy, p.book, p.opened_at from positions p "
            "where p.status = 'OPEN' and date(p.opened_at) < ? "
            "and not exists (select 1 from checks c where c.position_id = p.id "
            "and date(c.checked_at) = ?)",
            (self.date, self.date),
        )
        if not unmarked:
            self.add(PASS, "book coverage", "every position open before the date was marked")
        else:
            self.add(
                FAIL,
                "book coverage",
                f"{len(unmarked)} open position(s) received no reading and were not opened that day",
                str([f"{r['ticker']}/{r['strategy']}/{r['book']}" for r in unmarked])[:300],
            )

    # ---------- A7: field completeness, with a control ----------

    def check_fields(self):
        rows = self.q(
            "select strategy, count(*) n, "
            "sum(case when delta is null then 1 else 0 end) d_null, "
            "sum(case when buffer_pct is null then 1 else 0 end) b_null "
            "from checks c join positions p on p.id = c.position_id "
            "where date(c.checked_at) = ? group by strategy order by strategy",
            (self.date,),
        )
        if not rows:
            self.add(SKIP, "field completeness", "no readings to measure")
            return
        ev = " ".join(
            f"{r['strategy']}:n{r['n']}/d{r['d_null']}/b{r['b_null']}" for r in rows
        )
        # LONG_CALL legitimately has no short strike to measure a buffer to.
        offenders = [
            r for r in rows
            if r["d_null"] or (r["b_null"] and "LONG" not in (r["strategy"] or ""))
        ]
        if not offenders:
            self.add(PASS, "field completeness", "greeks present; buffers present where meaningful", ev)
        else:
            self.add(
                WARN,
                "field completeness",
                f"{len(offenders)} strategy group(s) carry NULLs: "
                + ", ".join(r["strategy"] or "?" for r in offenders),
                ev,
            )
        self.blind_spot(
            "Multileg entry bid/ask is known to be 100% NULL (W98) and signals.willing_to_own "
            "is 100% NULL (W107). Neither is checked here, so neither can improve or degrade "
            "without this audit staying silent about it."
        )

    # ---------- A8 / A9: what the day booked ----------

    def check_closes(self):
        rows = self.q(
            "select ticker, book, exit_reason from positions where date(closed_at) = ?",
            (self.date,),
        )
        if not rows:
            self.add(SKIP, "closes labelled", "no positions closed on the audited date")
            return
        unlabelled = [r for r in rows if (r["exit_reason"] or "").strip().lower() in
                      {x for x in UNLABELLED_REASONS if x is not None} or r["exit_reason"] is None]
        if not unlabelled:
            self.add(PASS, "closes labelled", f"all {len(rows)} close(s) carry a real exit reason")
        else:
            self.add(
                FAIL,
                "closes labelled",
                f"{len(unlabelled)} of {len(rows)} close(s) carry no reason or the legacy 'manual'",
                str([r["ticker"] for r in unlabelled])[:200],
            )

    def check_origins(self):
        rows = self.q(
            "select ticker, book, origin_screen from positions where date(opened_at) = ?",
            (self.date,),
        )
        if not rows:
            self.add(SKIP, "origin census", "nothing opened on the audited date")
            return
        counts = OrderedDict()
        for r in rows:
            counts[r["origin_screen"] or "(null)"] = counts.get(r["origin_screen"] or "(null)", 0) + 1
        nulls = counts.get("(null)", 0)
        ev = " ".join(f"{k}:{v}" for k, v in counts.items())
        if nulls:
            self.add(WARN, "origin census", f"{nulls} of {len(rows)} opened without an origin stamp", ev)
        else:
            self.add(PASS, "origin census", f"{len(rows)} opened, all stamped", ev)

    # ---------- run ----------

    # ---------- per-agent brief (typed; consumed by the PG dashboard) ----------
    #
    # Everything below is TYPED. A UI must never parse the human-readable
    # strings in `results` -- that is scraping our own rendering, and it breaks
    # silently the day someone improves the wording.
    #
    # Three rules this block obeys:
    #   1. THREE states, never two. An agent that left no evidence is not
    #      "fine", and must not share a rendering with one that reported.
    #   2. The slot is DERIVED from started_at, never read from
    #      agent_runs.slot, which is NULL on a large minority of rows
    #      (measured s105: 8 of 28 snapshot.daily rows since 2026-08-01).
    #      A UI keyed on that column would lose runs silently.
    #   3. paper.exits' status word is derived on a different axis from every
    #      other agent and does not mean what it means elsewhere (W102). It is
    #      exposed as raw_status with its meaning stated, never as health.

    AGENT_SPEC = [
        ("com.helm.ivr.refresh", "ivr", ["09:35"],
         "Refreshes IV rank across the watchlist", "agent_runs"),
        ("com.helm.snapshot.daily", "snapshot", ["10:00", "12:30", "15:15"],
         "Journals a mark for every open position", "agent_runs"),
        ("com.helm.paper.exits", "exits", ["15:35"],
         "Applies the exit doctrine to the paper book", "agent_runs"),
        ("com.helm.mktsampler", None, [],
         "Samples the market every 10 minutes; the liveness witness",
         "sampler_csv"),
        ("com.helm.audit.eod", None, ["16:15"],
         "Audits whether the day collected properly", "self"),
    ]

    @staticmethod
    def _parse_notes(notes):
        """Pull the NAMES out of a run note. Returns (names, kind).

        A count is not a name. "82 of 83" hides that it is the SAME ticker
        failing every day, which is the difference between a transient failure
        and a structural exclusion.

        Two shapes are written today:
            snapshot: "not-journaled (13): AMD-IRON_CONDOR-...; DELL-..."
            ivr:      "SKHY"
        Anything unrecognised returns kind="raw" rather than being dropped.
        """
        if not notes:
            return [], "none"
        text = str(notes).strip()
        if ":" in text and "(" in text.split(":", 1)[0]:
            head, tail = text.split(":", 1)
            names = [x.strip() for x in tail.split(";") if x.strip()]
            return names, (head.split("(")[0].strip() or "listed")
        if text.lower().startswith("held "):
            return [], "held"
        parts = [x.strip() for x in text.replace(";", ",").split(",") if x.strip()]
        if parts and all(len(x) <= 12 and " " not in x for x in parts):
            return parts, "tickers"
        return [], "raw"

    @staticmethod
    def _claimed_count(notes):
        """The count a run note CLAIMS, e.g. 13 from "not-journaled (13): ...".

        Returned separately from the parsed names so the two can be compared.
        They do not always agree -- see the header of the patch that added
        this -- and a list that is quietly shorter than its own count is worse
        than no list, because it reads as complete.
        """
        if not notes:
            return None
        text = str(notes)
        if "(" not in text or ")" not in text:
            return None
        inner = text.split("(", 1)[1].split(")", 1)[0].strip()
        try:
            return int(inner)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _now_hhmm():
        return datetime.now().strftime("%H:%M")

    def _slot_due(self, nominal):
        """Has this slot's window closed yet?

        Only meaningful when auditing TODAY. A run may start up to
        SLOT_TOLERANCE_MIN late and still be its slot, so the slot is not
        overdue until that window has passed. Before then the absence of a run
        means "not yet", which is a different fact from "missing" and must
        never be rendered as one.
        """
        if not getattr(self, "is_today", False):
            return True
        return _mins(self._now_hhmm()) > _mins(nominal) + SLOT_TOLERANCE_MIN

    def _slot_for(self, started_at, nominals):
        """Nearest nominal slot, and the signed drift in minutes.

        Drift is a NUMBER on purpose. A 10:00 snapshot that launchd fired at
        14:20 when the lid opened produced a mark stamped with the wrong market
        moment -- a wrong number, not a missing one -- and a UI cannot show the
        severity of that from the string "ran at 14:20".
        """
        if not started_at or not nominals:
            return None, None
        actual = _mins(started_at[11:16])
        best = None
        drift = None
        for nominal in nominals:
            delta = actual - _mins(nominal)
            if drift is None or abs(delta) < abs(drift):
                best = nominal
                drift = delta
        return best, drift

    def build_sampler(self):
        """Typed connectivity timeline from the sampler CSV.

        machine_liveness() counts rows per hour and discards `connected` and
        `note`. That is enough to answer "was the machine up", which is all the
        assertions need, but not enough to answer "when was the broker
        unreachable" -- which is the question a trader actually has when a day
        looks thin.

        A SWEEP is one timestamp across all sampled tickers. A sweep counts as
        disconnected only when EVERY row in it is disconnected, so a single
        ticker failing does not manufacture an outage.
        """
        out = {
            "source": "logs/mktdata_samples.csv",
            "available": False,
            "note": ("The sampler is the load-bearing witness for machine "
                     "liveness and nothing witnesses the sampler. Its silence "
                     "cannot distinguish 'machine off' from 'sampler dead'."),
            "sweeps": 0,
            "rth_sweeps": 0,
            "rth_sweeps_normal": "123-126 ROWS/day; ~41 sweeps",
            "connected_sweeps": 0,
            "disconnected_sweeps": 0,
            "first_ts": None,
            "last_ts": None,
            "hours": [],
            "outages": [],
        }
        if not SAMPLER_CSV.exists():
            return out

        sweeps = {}
        try:
            with open(SAMPLER_CSV, errors="replace") as fh:
                for row in csv.DictReader(fh):
                    ts = row.get("ts", "") or ""
                    if not ts.startswith(self.date):
                        continue
                    live = str(row.get("connected", "")).strip() == "1"
                    rec = sweeps.setdefault(ts, {"live": False, "rows": 0,
                                                 "note": ""})
                    rec["rows"] += 1
                    if live:
                        rec["live"] = True
                    elif not rec["note"]:
                        rec["note"] = (row.get("note") or "")[:80]
        except Exception:
            return out

        out["available"] = True
        if not sweeps:
            return out

        keys = sorted(sweeps)
        out["sweeps"] = len(keys)
        out["first_ts"] = keys[0]
        out["last_ts"] = keys[-1]

        hours = {}
        for ts in keys:
            hh = ts[11:13]
            h = hours.setdefault(hh, {"hh": hh, "sweeps": 0, "connected": 0,
                                      "disconnected": 0})
            h["sweeps"] += 1
            if sweeps[ts]["live"]:
                h["connected"] += 1
                out["connected_sweeps"] += 1
            else:
                h["disconnected"] += 1
                out["disconnected_sweeps"] += 1
            if "09:30" <= ts[11:16] <= "16:00":
                out["rth_sweeps"] += 1
        out["hours"] = [hours[k] for k in sorted(hours)]

        # Contiguous runs of fully-disconnected sweeps.
        start = None
        last = None
        reason = ""
        for ts in keys:
            if not sweeps[ts]["live"]:
                if start is None:
                    start = ts
                    reason = sweeps[ts]["note"]
                last = ts
            elif start is not None:
                out["outages"].append({"from": start[11:16], "to": last[11:16],
                                       "sweeps": 0, "reason": reason})
                start = None
        if start is not None:
            out["outages"].append({"from": start[11:16], "to": last[11:16],
                                   "sweeps": 0, "reason": reason})
        for o in out["outages"]:
            o["sweeps"] = sum(
                1 for ts in keys
                if o["from"] <= ts[11:16] <= o["to"] and not sweeps[ts]["live"]
            )
            # An outage matters when it overlaps RTH. Outside it, a shut
            # gateway is the normal overnight state, not a fault.
            o["in_rth"] = bool(o["from"] <= "16:00" and o["to"] >= "09:30")
        out["rth_outages"] = [o for o in out["outages"] if o["in_rth"]]
        out["rth_outage_sweeps"] = sum(
            o["sweeps"] for o in out["rth_outages"]
        )
        return out

    def build_agents(self, is_today):
        """Typed per-agent brief. Called only on the --json path."""
        session = getattr(self, "is_session", True)
        rows = self.q(
            "select agent, started_at, status, attempted, journaled, failed, "
            "notes from agent_runs where date(started_at) = ? "
            "order by started_at",
            (self.date,),
        )

        agents = {}
        for label, match, nominals, role, source in self.AGENT_SPEC:
            entry = {
                "label": label.replace("com.helm.", ""),
                "role": role,
                "evidence_source": source,
                "expected_slots": list(nominals),
                "runs": [],
                "missing_slots": [],
                "state": "",
                "state_reason": "",
            }

            if source != "agent_runs":
                # These two write no run row BY DESIGN. Absence from the ledger
                # is expected here, and rendering it as a missed run would be
                # the same defect this block exists to prevent, inverted.
                entry["state"] = "NO_LEDGER_BY_DESIGN"
                entry["state_reason"] = (
                    "samples to logs/mktdata_samples.csv and writes no run row"
                    if source == "sampler_csv"
                    else "runs read-only and records nothing about itself"
                )
                agents[label] = entry
                continue

            mine = [r for r in rows if match in (r["agent"] or "")]
            for r in mine:
                slot, drift = self._slot_for(r["started_at"], nominals)
                names, kind = self._parse_notes(r["notes"])
                entry["runs"].append({
                    "started_at": r["started_at"],
                    "slot": slot,
                    "drift_min": drift,
                    "mistimed": bool(
                        drift is not None and abs(drift) > SLOT_TOLERANCE_MIN
                    ),
                    "raw_status": r["status"],
                    "attempted": r["attempted"],
                    "journaled": r["journaled"],
                    "failed": r["failed"],
                    "names": names,
                    "names_kind": kind,
                    "names_claimed": self._claimed_count(r["notes"]),
                    "names_incomplete": bool(
                        self._claimed_count(r["notes"]) is not None
                        and self._claimed_count(r["notes"]) != len(names)
                    ),
                })

            landed = set()
            for r in entry["runs"]:
                if r["slot"] and not r["mistimed"]:
                    landed.add(r["slot"])
            stood_down = any(
                r["raw_status"] == "SKIPPED_CLOSED" for r in entry["runs"]
            )

            if stood_down:
                entry["state"] = "STOOD_DOWN"
                entry["state_reason"] = "recorded SKIPPED_CLOSED"
            elif not session:
                entry["state"] = "NO_EVIDENCE"
                entry["state_reason"] = (
                    "non-session day, and this agent recorded nothing at all"
                )
            elif not entry["runs"] and not any(
                    self._slot_due(s) for s in nominals):
                entry["state"] = "NOT_DUE"
                entry["state_reason"] = (
                    "nothing scheduled has come round yet today: "
                    + ", ".join(nominals)
                )
                entry["pending_slots"] = list(nominals)
            elif not entry["runs"]:
                entry["state"] = "NO_EVIDENCE"
                entry["state_reason"] = (
                    "no run row for this date. An agent that cannot reach the "
                    "broker exits WITHOUT writing one, so this is "
                    "indistinguishable from the agent never having been "
                    "loaded. Read machine liveness before concluding the agent "
                    "is broken."
                )
            else:
                absent = [s for s in nominals if s not in landed]
                entry["pending_slots"] = [
                    s for s in absent if not self._slot_due(s)
                ]
                entry["missing_slots"] = [
                    s for s in absent if self._slot_due(s)
                ]
                if entry["pending_slots"] and not entry["missing_slots"]:
                    entry["state"] = "IN_PROGRESS"
                    entry["state_reason"] = (
                        "still to come today: " + ", ".join(entry["pending_slots"])
                    )
                elif entry["missing_slots"]:
                    entry["state"] = "PARTIAL_SLOTS"
                    entry["state_reason"] = (
                        "no run row landed on: "
                        + ", ".join(entry["missing_slots"])
                    )
                else:
                    entry["state"] = "REPORTED"
                    entry["state_reason"] = (
                        "every expected slot has a run row within tolerance"
                    )

            agents[label] = entry

        # paper.exits: its status word is meaningless, and what the agent
        # actually DID is not in the ledger at all -- it is in the book.
        # ---- outcome: a SECOND axis, deliberately not folded into state ----
        #
        # state   answers "did the agent report?"
        # outcome answers "did it produce anything?"
        #
        # These are different questions and 2026-08-12 is the proof: three
        # snapshot runs landed exactly on their slots and journaled NOTHING
        # through a broker outage. On one axis that day is indistinguishable
        # from a clean one. Measured s105 -- the first cut of this block did
        # exactly that, which is why the axis exists.
        for _label, _e in agents.items():
            _e["attempted_total"] = None
            _e["journaled_total"] = None
            _e["lost"] = None

            if _e["state"] == "NO_LEDGER_BY_DESIGN":
                _e["outcome"] = "NOT_APPLICABLE"
                _e["outcome_reason"] = "no run ledger to judge work from"
                continue
            if _e["state"] == "STOOD_DOWN":
                _e["outcome"] = "STOOD_DOWN"
                _e["outcome_reason"] = (
                    "market closed; the agent declined to work, as designed"
                )
                continue
            if _e["state"] in ("NOT_DUE", "IN_PROGRESS") and not _e["runs"]:
                _e["outcome"] = "PENDING"
                _e["outcome_reason"] = "the day is not finished"
                continue
            if not _e["runs"]:
                _e["outcome"] = "NO_RUNS"
                _e["outcome_reason"] = (
                    "nothing to judge -- the agent left no row at all"
                )
                continue
            if _label == "com.helm.paper.exits":
                # Its attempted/journaled are evaluated/closed, so summing them
                # as work would repeat the very error this brief documents.
                _e["outcome"] = "SEE_CLOSED"
                _e["outcome_reason"] = (
                    "this agent's attempted/journaled mean EVALUATED/CLOSED, "
                    "so they cannot be read as work done. Read closed_count "
                    "and closed[] instead."
                )
                continue

            _att = sum((r["attempted"] or 0) for r in _e["runs"])
            _jou = sum((r["journaled"] or 0) for r in _e["runs"])
            _e["attempted_total"] = _att
            _e["journaled_total"] = _jou
            _e["lost"] = _att - _jou

            if _att == 0:
                _e["outcome"] = "NOTHING_ATTEMPTED"
                _e["outcome_reason"] = "the agent ran but had nothing to do"
            elif _jou == 0:
                _e["outcome"] = "NOTHING_JOURNALED"
                _e["outcome_reason"] = (
                    "ran %d time(s) and recorded NOTHING. It reported; it did "
                    "not work." % len(_e["runs"])
                )
            elif _jou < _att:
                _e["outcome"] = "SHORTFALL"
                _e["outcome_reason"] = (
                    "%d of %d journaled; %d lost" % (_jou, _att, _att - _jou)
                )
            else:
                _e["outcome"] = "COMPLETE"
                _e["outcome_reason"] = "%d of %d journaled" % (_jou, _att)

        exits = agents.get("com.helm.paper.exits")
        if exits is not None:
            exits["status_meaning"] = (
                "attempted = positions EVALUATED, journaled = positions "
                "CLOSED. Derived on a different axis from every other agent, "
                "so the status word does not mean here what it means "
                "elsewhere. Do not render it as health."
            )
            closed = self.q(
                "select ticker, strategy, exit_reason, realized_pnl, "
                "closed_at from positions "
                "where date(closed_at) = ? and book = 'PAPER' "
                "order by closed_at",
                (self.date,),
            )
            exits["closed"] = [{
                "ticker": c["ticker"],
                "strategy": c["strategy"],
                "reason": c["exit_reason"],
                "pnl": (round(c["realized_pnl"], 2)
                        if c["realized_pnl"] is not None else None),
                "at": c["closed_at"],
            } for c in closed]
            exits["closed_count"] = len(exits["closed"])
            exits["not_captured"] = (
                "Near-misses are not recorded anywhere. A position that came "
                "within a whisker of an exit rule firing appears nowhere in "
                "this brief, because the agent does not journal what it "
                "considered and declined."
            )

        self.agents = agents
        self.blind_spot(
            "The per-agent brief reports what each agent RECORDED, not what it "
            "decided. An agent that died before writing its row is reported as "
            "NO_EVIDENCE, which is honest but is not the same as knowing it "
            "failed."
        )
        return agents

    def run(self, is_today):
        self.is_today = is_today
        self.check_session()
        self.check_roster(is_today)
        if not getattr(self, "is_session", True):
            self.check_stand_down()
            return
        self.load_runs()
        self.machine_liveness()
        self.check_machine()
        self.check_slots()
        self.check_broker()
        self.check_coverage()
        self.check_marks()
        self.check_fields()
        self.check_closes()
        self.check_origins()
        self.blind_spot(
            "Machine liveness is inferred from the SAMPLER, not from the OS. If the sampler "
            "alone died while the Mac stayed up, this audit will excuse missing slots as "
            "'machine down' when an agent really was broken. The sampler is the load-bearing "
            "witness here, and nothing witnesses the sampler."
        )
        self.blind_spot(
            "This audit reads what the database RECORDED. It cannot see a reading that was "
            "correct but never attempted, or a ticker dropped before any row was written (W112)."
        )


def render(a, clock):
    L = []
    L.append("=" * 72)
    L.append(f"HELM end-of-day collection audit — {a.date}")
    L.append(f"run at {clock}   db={DB}   (read-only)")
    L.append("=" * 72)
    L.append("")
    counts = OrderedDict((k, 0) for k in (PASS, FAIL, WARN, SKIP))
    for r in a.results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
        L.append(f"[{r['status']:4}] {r['name']}")
        L.append(f"        {r['detail']}")
        if r["evidence"]:
            L.append(f"        evidence: {r['evidence']}")
    L.append("")
    L.append("-" * 72)
    L.append("  ".join(f"{k}: {v}" for k, v in counts.items()))
    if a.machine_offline_day:
        verdict = ("INCONCLUSIVE — the machine was off or asleep for the session. "
                   "The data is missing, but nothing in HELM is broken.")
    elif a.failed:
        verdict = "FAIL — the day did not collect as designed"
    else:
        verdict = "PASS — the day collected as designed"
    L.append("VERDICT: " + verdict)
    L.append("-" * 72)
    L.append("")
    L.append("BLIND SPOTS — what a PASS above does NOT tell you:")
    for b in a.blind:
        L.append(f"  - {b}")
    L.append("")
    return "\n".join(L)


def run():
    ap = argparse.ArgumentParser(prog="helm audit", description="HELM audits")
    ap.add_argument("what", nargs="?", default="eod", choices=["eod"])
    ap.add_argument("--date", help="YYYY-MM-DD (default: today)")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of text")
    ap.add_argument("--no-report", action="store_true", help="do not write the dated report file")
    args = ap.parse_args()

    now = datetime.now()
    date_str = args.date or now.strftime("%Y-%m-%d")
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        print(f"bad --date: {date_str}", file=sys.stderr)
        sys.exit(2)

    if not DB.exists():
        print(f"database not found: {DB}", file=sys.stderr)
        sys.exit(2)

    a = Audit(date_str)
    is_today = date_str == now.strftime("%Y-%m-%d")
    a.run(is_today=is_today)
    # Auditing a day that has not finished happening yet reports absence as failure.
    # This also catches the launchd catch-up case: a missed 16:15 slot fires at WAKE,
    # possibly the NEXT morning, where "today" is a day whose slots have not run.
    if is_today and now.strftime("%H:%M") < "15:40":
        a.results.insert(0, {
            "status": WARN, "name": "audit timing",
            "detail": f"run at {now.strftime('%H:%M')}, before the day's last scheduled writer "
                      "(paper exits 15:35). Slots that have not come round yet are reported "
                      "as missing. Re-run after 15:40, or pass --date for a finished day.",
            "evidence": "",
        })
    clock = now.strftime("%Y-%m-%d %H:%M:%S %a")

    if args.json:
        a.build_agents(is_today)
        a.sampler = a.build_sampler()
        print(_json.dumps(
            {"date": date_str, "run_at": clock, "failed": a.failed,
             "agents": a.agents,
             "sampler": a.sampler,
             "results": a.results, "blind_spots": a.blind}, indent=2))
    else:
        text = render(a, clock)
        print(text)
        if not args.no_report:
            try:
                LOGS.mkdir(exist_ok=True)
                out = LOGS / f"audit_eod_{date_str}.txt"
                out.write_text(text)
                print(f"report written: {out}")
            except Exception as e:
                print(f"(could not write report: {e})", file=sys.stderr)

    sys.exit(1 if a.failed else 0)


if __name__ == "__main__":
    run()
