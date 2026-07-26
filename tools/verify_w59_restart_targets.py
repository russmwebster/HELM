#!/usr/bin/env python3
"""W59 — verify `helm restart <target>`: the right label, and no false success.

Behavioural, not structural. launchctl and the network are both faked, so every
check watches what the command *did*: which label it would have kicked, whether
it kicked anything at all before validating its input, and what it exits with.

Four properties earn the harness:

  1. BACKWARD COMPATIBILITY. `helm restart` with no arguments must still kick
     com.helm.server and nothing else. That spelling is in muscle memory and in
     helm-servers.sh's deprecation notice.
  2. THE FIREWALL. A label is never taken from the command line. An unknown or
     COTS-shaped target must be refused BEFORE launchctl is called — refusing
     after acting is not refusing.
  3. NO FALSE SUCCESS (the W35 lesson). kickstart returning 0 while the port
     stays silent must exit non-zero. "The command exited 0" and "the service
     is answering" are different claims and the exit code must mean the second.
  4. THE PROBE IS NOT ITSELF A FALSE NEGATIVE. The engine server is reached at
     helm.local; failing to connect on 127.0.0.1 must not be read as down.

  python3.12 verify_w59_restart_targets.py [HELM_ROOT]
"""
import importlib.util
import sys
import types

HELM = sys.argv[1] if len(sys.argv) > 1 else "/mnt/user-data/uploads/helm"
SRC = f"{HELM}/helm/cli/server_cmd.py"

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(f"{name}{(' — ' + detail) if detail else ''}")


# --- rich, stubbed so the module imports and we can read what it printed ----- #
OUT = []
for n, attrs in [("rich", {}), ("rich.console", {}),
                 ("rich.box", {"SIMPLE": object()}),
                 ("rich.table", {})]:
    m = types.ModuleType(n)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules.setdefault(n, m)


class _T:
    def __init__(self, *a, **k):
        self.rows = []

    def add_column(self, *a, **k):
        pass

    def add_row(self, *cells, **k):
        self.rows.append(cells)


sys.modules["rich.table"].Table = _T
sys.modules["rich.console"].Console = lambda *a, **k: types.SimpleNamespace(
    print=lambda *a, **k: OUT.append(str(a[0]) if a else ""))
sys.modules["rich"].box = sys.modules["rich.box"]

try:
    spec = importlib.util.spec_from_file_location("sc59", SRC)
    sc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sc)
    check("server_cmd imports", True)
    ok = True
except Exception as e:
    check("server_cmd imports", False, f"{type(e).__name__}: {e}")
    ok = False

# --- fakes ------------------------------------------------------------------ #
KICKED = []            # every launchctl argv the module ran
OPEN_ENDPOINTS = set()  # (host, port) pairs the fake network will accept


class FakeSub:
    class CompletedProcess:
        def __init__(self, rc, out="", err=""):
            self.returncode, self.stdout, self.stderr = rc, out, err

    rc = 0
    stderr = ""

    @classmethod
    def run(cls, argv, **kw):
        KICKED.append(list(argv))
        return cls.CompletedProcess(cls.rc, "", cls.stderr)


class FakeSocket:
    @staticmethod
    def create_connection(addr, timeout=None):
        if tuple(addr) in OPEN_ENDPOINTS:
            class _S:
                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False
            return _S()
        raise OSError("refused")


def invoke(argv_tail, rc=0, up=(), platform="darwin"):
    """Run run() with a faked world. Returns (exit_code, labels_kicked)."""
    KICKED.clear()
    OUT.clear()
    OPEN_ENDPOINTS.clear()
    OPEN_ENDPOINTS.update(up)
    FakeSub.rc = rc
    sc.subprocess = FakeSub
    sc.socket = FakeSocket
    sc.PROBE_TIMEOUT = 0.4          # keep the harness fast
    old_argv, old_plat = sys.argv, sys.platform
    sys.argv = ["helm restart"] + list(argv_tail)
    try:
        sys.platform = platform
    except Exception:
        pass
    code = 0
    try:
        sc.run()
    except SystemExit as e:
        code = e.code if e.code is not None else 0
    finally:
        sys.argv = old_argv
        try:
            sys.platform = old_plat
        except Exception:
            pass
    labels = [a[-1].split("/")[-1] for a in KICKED if len(a) >= 4]
    return code, labels


ALL_UP = {("helm.local", 8766), ("127.0.0.1", 8766),
          ("127.0.0.1", 8770), ("127.0.0.1", 8771)}

# getattr, not attribute access: on the pre-change module these do not exist,
# and the control run must REPORT that rather than die on an AttributeError.
# Third time this lesson has come up (W6/W7/W8 used .index() where .find() was
# needed) — a harness that crashes proves nothing about what it crashed on.
TG = getattr(sc, "TARGETS", {}) if ok else {}
AL = getattr(sc, "ALIASES", {}) if ok else {}
if ok:
    check("a TARGETS table exists", bool(TG), "absent")
    check("an ALIASES map exists", isinstance(AL, dict) and bool(AL), "absent")

if ok:
    # 1 — backward compatibility -------------------------------------------- #
    code, labels = invoke([], up=ALL_UP)
    check("no arguments still restarts the engine server, and only that",
          labels == ["com.helm.server"], str(labels))
    check("no arguments exits 0 when it comes back up", code == 0, str(code))

    # 2 — the new targets ---------------------------------------------------- #
    code, labels = invoke(["pg"], up=ALL_UP)
    check("`pg` restarts com.helm.pg, and only that",
          labels == ["com.helm.pg"], str(labels))
    check("`pg` exits 0 when the dashboard answers", code == 0, str(code))

    _, labels = invoke(["trial"], up=ALL_UP)
    check("`trial` restarts com.helm.trial.ui", labels == ["com.helm.trial.ui"],
          str(labels))

    for alias in ("web", "ui", "dashboard", "desktop", "helm-pg", "PG", "Pg"):
        _, labels = invoke([alias], up=ALL_UP)
        check(f"`{alias}` resolves to the web dashboard",
              labels == ["com.helm.pg"], str(labels))

    _, labels = invoke(["all"], up=ALL_UP)
    check("`all` restarts all three HELM agents",
          labels == ["com.helm.server", "com.helm.pg", "com.helm.trial.ui"],
          str(labels))
    check("`all` covers every target in the table",
          bool(TG) and len(labels) == len(TG), f"{len(labels)} vs {len(TG)}")

    _, labels = invoke(["pg", "pg", "web"], up=ALL_UP)
    check("a target named twice is kicked once", labels == ["com.helm.pg"],
          str(labels))

    # 3 — the firewall: refuse BEFORE acting --------------------------------- #
    for bad in ("cots", "com.cots.server", "COTS", "cots.local",
                "com.helm.server", "nonsense", "", "--force"):
        code, labels = invoke([bad] if bad else ["  "], up=ALL_UP)
        if bad == "":
            continue
        check(f"`{bad}` is refused", code == 2, f"exit {code}")
        check(f"`{bad}` kicked NOTHING — refusal happens before launchctl",
              labels == [], str(labels))

    check("no target in the table is outside com.helm.*",
          bool(TG) and all(v[0].startswith("com.helm.") for v in TG.values()),
          str([v[0] for v in TG.values()]))
    check("nothing in the table mentions cots",
          bool(TG) and not any("cots" in str(v).lower() for v in TG.values()))
    check("no alias maps to a target that does not exist",
          bool(AL) and all(v in TG for v in AL.values()), str(AL))
    # a raw launchd label must NOT be accepted even though it is a real HELM one:
    # the point is that labels come from the table, not the command line.
    _, labels = invoke(["com.helm.pg"], up=ALL_UP)
    check("a raw launchd label is not an accepted target", labels == [],
          str(labels))

    # and if the table itself were ever poisoned, _kickstart still refuses
    saved = dict(TG)
    try:
        if not TG:
            raise RuntimeError("no TARGETS table to poison")
        sc.TARGETS["evil"] = ("com.cots.server", "not ours", ("127.0.0.1",),
                              8765, "http://cots.local:8765", "")
        KICKED.clear()
        sc.subprocess = FakeSub
        sc.socket = FakeSocket
        res = sc._kickstart(types.SimpleNamespace(print=OUT.append), "evil")
        check("a non-com.helm label is refused at the point of use, even if it "
              "reaches the table", res is False and KICKED == [], str(KICKED))
    except Exception as _e:
        check("a non-com.helm label is refused at the point of use, even if it "
              "reaches the table", False, f"{type(_e).__name__}: {_e}")
    finally:
        if TG:
            sc.TARGETS.clear()
            sc.TARGETS.update(saved)

    # 4 — no false success --------------------------------------------------- #
    code, labels = invoke(["pg"], rc=1, up=ALL_UP)
    check("a failing kickstart exits non-zero", code != 0, str(code))

    code, labels = invoke(["pg"], rc=0, up={("helm.local", 8766)})
    check("kickstart returning 0 while the port stays silent is NOT success",
          code != 0, f"exit {code}")
    check("...and it says so rather than claiming up",
          any("not answering" in o for o in OUT), " | ".join(OUT[-3:]))
    check("...and it points at the log to look in",
          any("pg.log" in o for o in OUT), " | ".join(OUT[-3:]))

    code, _ = invoke(["all"], rc=0, up={("127.0.0.1", 8770)})
    check("`all` exits non-zero if any one service fails to answer", code != 0,
          str(code))

    # 5 — the probe must not manufacture a false negative -------------------- #
    code, labels = invoke([], up={("helm.local", 8766)})
    check("the engine server counts as up when only helm.local answers",
          code == 0, f"exit {code}")
    _srv = TG.get("server") or ()
    check("the engine server probes helm.local, not just loopback",
          len(_srv) > 2 and "helm.local" in _srv[2], str(_srv[2:3]))

    # 6 — list and help act on nothing --------------------------------------- #
    for a in (["list"], ["--list"], ["ls"], ["help"], ["--help"], ["-h"]):
        code, labels = invoke(a, up=ALL_UP)
        check(f"`{a[0]}` restarts nothing", labels == [], str(labels))
        check(f"`{a[0]}` is not an error", code == 0, str(code))

    # 7 — non-macOS refuses -------------------------------------------------- #
    code, labels = invoke(["pg"], up=ALL_UP, platform="linux")
    check("off macOS it refuses instead of shelling out to a missing launchctl",
          code == 1 and labels == [], f"exit {code} kicked {labels}")

    # 8 — it kicks with -k (kill and relaunch), not plain kickstart ---------- #
    invoke(["pg"], up=ALL_UP)
    check("launchctl is called as `kickstart -k` so code is re-read",
          KICKED and KICKED[0][:3] == ["launchctl", "kickstart", "-k"],
          str(KICKED[:1]))
    check("the target is scoped to this user's gui domain",
          KICKED and KICKED[0][3].startswith("gui/"), str(KICKED[:1]))

# --- and the entry point still points here ---------------------------------- #
try:
    hp = open(f"{HELM}/helm.py", encoding="utf-8").read()
    check("helm.py still routes `restart` to server_cmd",
          "'restart':" in hp and "helm.cli.server_cmd" in hp)
    check("the help line mentions the targets rather than one agent",
          "restart" in hp and ("pg" in hp.split("'restart':")[1][:200]
                               or "web" in hp.split("'restart':")[1][:200]),
          hp.split("'restart':")[1][:120] if "'restart':" in hp else "")
except OSError as e:
    check("helm.py readable", False, str(e))

print()
for line in PASS:
    print(f"  ok    {line}")
for line in FAIL:
    print(f"  FAIL  {line}")
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
