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

    def run(self, is_today):
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
        print(_json.dumps(
            {"date": date_str, "run_at": clock, "failed": a.failed,
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
