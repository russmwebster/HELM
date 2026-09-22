"""W179 -- a one-sided IBKR quote (bid -1 = no bid, live ask) must yield a mark.

Usage:  HELM_ROOT=~/Projects/helm python3 tools/verify_w179_nobid.py helm/cli/check_cmd.py
        ...                        python3 tools/verify_w179_nobid.py <path to a .bak>   (control: expect FAIL)

Stubs helm.ibkr and ib_insync so the REAL fetch_ibkr_option body runs with a
scripted ticker. Cases: ABT (no bid, ask 0.90), VALE (no bid, ask 0.01), the
closed-market case (no bid, no ask -> no mark), a two-sided control, and a NaN
bid. Prints the file it loaded. Run the control FIRST and the patched file LAST.
"""
import sys, types, math, importlib, os
sys.path.insert(0, ".")
target = sys.argv[1]   # module path to load: helm/cli/check_cmd.py or the .bak
# --- stubs so the real fetch_ibkr_option body runs without a gateway ---
ibkr = types.ModuleType("helm.ibkr"); ibkr.check_connection = lambda: {"connected": True}
sys.modules["helm.ibkr"] = ibkr
class _G:
    def __init__(s): s.delta=0.04; s.theta=-0.02; s.gamma=0.02; s.vega=0.02; s.impliedVol=0.28
class _T:
    def __init__(s,b,a,l): s.bid=b; s.ask=a; s.last=l; s.modelGreeks=_G()
class _IB:
    quote=None
    def connect(s,*a,**k): pass
    def qualifyContracts(s,*a): pass
    def reqMarketDataType(s,*a): pass
    def reqMktData(s,*a): return _IB.quote
    def sleep(s,*a): pass
    def disconnect(s): pass
ibi = types.ModuleType("ib_insync"); ibi.IB=_IB; ibi.Option=lambda *a,**k: None
sys.modules["ib_insync"] = ibi
# load the requested file as module 'cc'
from importlib.machinery import SourceFileLoader; spec = importlib.util.spec_from_loader("cc", SourceFileLoader("cc", target))
cc = importlib.util.module_from_spec(spec); spec.loader.exec_module(cc)
print("loaded:", os.path.abspath(target))
cases = [
  ("no bid, live ask (ABT)",      (-1.0, 0.90, float('nan')), 0.0,  0.90, 0.45),
  ("no bid, live ask (VALE)",     (-1.0, 0.01, 0.01),         0.0,  0.01, 0.01),
  ("no bid, no ask (closed)",     (-1.0, -1.0, 3.10),         None, None, None),
  ("two-sided (DVN control)",     (0.20, 0.25, 0.22),         0.20, 0.25, 0.23),
  ("nan bid, live ask",           (float('nan'), 0.50, None), None, 0.50, None),
]
ok = True
for name,(b,a,l),eb,ea,em in cases:
    _IB.quote = _T(b,a,l)
    r = cc.fetch_ibkr_option("X","2026-10-02",112.0,"CALL")
    got = (r["bid"], r["ask"], r["mid"])
    exp = (eb,ea,em)
    flag = "PASS" if got==exp else "FAIL"
    ok &= got==exp
    print(f"{flag}  {name:28s} got bid/ask/mid={got}  expected={exp}")
print("RESULT:", "PASS" if ok else "FAIL")
