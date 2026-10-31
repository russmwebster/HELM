"""s116 both-ways verification. Runs the four scenarios against a tree and
asserts what each must produce. Every case runs in its own process against a
FRESH VACUUM copy of the live DB, with every quote source faked.

  python3 verify_s116.py <tree>            -- expect PASS on /tmp/v_post
                                              and the pre-change failures on /tmp/v_pre
"""
import json, subprocess, sys

TREE = sys.argv[1]
POST = "--pre" not in sys.argv

# (case, expectation) -- written for the PATCHED tree.
EXPECT = {
 "bx_settled": {
   "check_persisted": 1,
   "stored_pnl": -246.0,          # long +247, settled short -493 (close_price 8.28)
   "stored_dq": "GOOD",
   "leg_rows_written": 2,
   "leg_dq": {"PARTIAL"},
   "leg_prices": {"LONG": 20.27, "SHORT": 8.28},
   "primary_direction": "LONG",
 },
 "aa_short_expired": {           # the state both REAL diagonals reach 2026-10-16
   "check_persisted": 1,
   "stored_pnl": -1075.0,
   "stored_dte_now": 102,         # the LIVE leg's, not the dead one's
   "stored_dq": "GOOD",
   "leg_rows_written": 2,
   "leg_dq": {"PARTIAL"},
   "leg_prices": {"LONG": 7.5, "SHORT": 3.0},
   "primary_direction": "LONG",
 },
 "live_control": {               # both legs alive: nothing may move
   "check_persisted": 1,
   "stored_pnl": -110.0,
   "stored_dte_now": 39,
   "stored_dq": "GOOD",
   "leg_rows_written": 2,
   "leg_dq": {"GOOD"},
   "leg_prices": {"LONG": 5.55, "SHORT": 1.32},
   "primary_direction": "SHORT",
 },
 "unquotable_leg": {             # a leg with NO mark: still nothing, either table
   "check_persisted": 0,
   "leg_rows_written": 0,
 },
}

npass = nfail = 0
def check(label, got, want):
    global npass, nfail
    if got == want:
        npass += 1
    else:
        nfail += 1
        print(f"  FAIL {label}: got {got!r}, expected {want!r}")

for case, exp in EXPECT.items():
    r = subprocess.run([sys.executable, "/tmp/harness.py", TREE, case],
                       capture_output=True, text=True)
    try:
        d = json.loads(r.stdout)
    except Exception:
        nfail += 1
        print(f"  FAIL {case}: harness produced no JSON\n{r.stderr[-400:]}")
        continue
    # loaded-code probe: assert WHICH tree ran, from the loaded module (s115)
    check(f"{case}/loaded-from", d["probe"]["check_cmd_file"].startswith(TREE), True)
    check(f"{case}/settled_mark-present", d["probe"]["has_settled_mark"], POST)
    check(f"{case}/no-exception", d["error"], None)
    for k in ("check_persisted", "stored_pnl", "stored_dte_now",
              "stored_dq", "leg_rows_written", "primary_direction"):
        if k in exp:
            check(f"{case}/{k}", d[k], exp[k])
    if exp.get("leg_rows_written"):
        rows = d["leg_rows"]
        check(f"{case}/leg-dq", {r_["data_quality"] for r_ in rows}, exp["leg_dq"])
        check(f"{case}/leg-prices",
              {r_["direction"]: r_["current_price"] for r_ in rows}, exp["leg_prices"])

print(f"\n{TREE}: PASS {npass} · FAIL {nfail}")
sys.exit(1 if nfail else 0)
