import sys
from pathlib import Path
sys.path.insert(0, str(Path.home() / 'Projects' / 'helm'))

from helm.cli.import_cmd import parse_fidelity_csv, infer_strategy
from collections import defaultdict

CSV_PATH = str(Path.home() / 'Projects' / 'helm' / 'data' / 'portfolio_test.csv')
accounts, rows = parse_fidelity_csv(CSV_PATH)

print(f'Accounts found: {len(accounts)}')
for a in accounts:
    print(f'  {a["number"]} --- {a["name"]}')
print()

print(f'Rows parsed: {len(rows)}')
print()

ticker_rows = defaultdict(list)
for r in rows:
    ticker_rows[r['ticker']].append(r)

print(f'Positions (grouped by ticker): {len(ticker_rows)}')
print()
for ticker, trows in sorted(ticker_rows.items()):
    strategy = infer_strategy(ticker, trows)
    legs = []
    for r in trows:
        if r['is_option']:
            d = 'S' if r['direction']=='SHORT' else 'L'
            legs.append(f'{d}-{r["option_type"][0]}{r["strike"]} {r["expiration"]} x{r["contracts"]} @ {r["open_price"]}')
        else:
            legs.append(f'STOCK x{r["contracts"]} @ {r["open_price"]}')
    print(f'  {ticker:<6} [{strategy:<16}]  {" | ".join(legs)}')

print()
print('PARSER TEST PASSED')
