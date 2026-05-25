
import logging, warnings
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
warnings.filterwarnings("ignore")
import yfinance as yf
from datetime import date, datetime

for ticker in ["TMUS", "WBD", "CMCSA"]:
    tk = yf.Ticker(ticker)
    exps = tk.options
    print(f"{ticker}: {len(exps)} expiries available")
    
    # Sum OI across first 4 liquid expiries (same as helm screen)
    today = date.today()
    liquid = [e for e in exps if (datetime.strptime(e, "%Y-%m-%d").date() - today).days >= 7][:4]
    total_oi = 0
    total_vol = 0
    for exp in liquid:
        chain = tk.option_chain(exp)
        oi = int(chain.calls["openInterest"].fillna(0).sum() + chain.puts["openInterest"].fillna(0).sum())
        vol = int(chain.calls["volume"].fillna(0).sum() + chain.puts["volume"].fillna(0).sum())
        total_oi += oi
        total_vol += vol
        print(f"  {exp}: OI={oi:,}  Vol={vol:,}")
    print(f"  TOTAL across {len(liquid)} expiries: OI={total_oi:,}  Vol={total_vol:,}")
    print()
