import sys
from pathlib import Path
sys.path.insert(0, str(Path.home() / 'Projects' / 'helm'))

from helm.models.watchlist import WatchlistItem
from helm.models.position import Position

# Clear any existing watchlist entries from test runs
from helm.db import transaction
with transaction() as conn:
    conn.execute('DELETE FROM watchlist')

# Test batch add via the parse_tickers function
from helm.cli.watchlist import parse_tickers, cmd_add

tickers = parse_tickers('NVDA,AAPL,MSFT,GOOGL,META')
assert tickers == ['NVDA','AAPL','MSFT','GOOGL','META'], f'Parse failed: {tickers}'
print('parse_tickers OK:', tickers)

# Test cmd_add with comma-delimited
sys.argv = ['helm watchlist', 'NVDA,AAPL,MSFT', '--sector', 'Technology']
cmd_add(['NVDA,AAPL,MSFT', '--sector', 'Technology'])

items = WatchlistItem.all()
assert len(items) == 3, f'Expected 3, got {len(items)}'
assert all(i.sector == 'Technology' for i in items)
print('cmd_add OK:', [i.ticker for i in items])

# Test add more with spaces
cmd_add(['GOOGL META', '--no-wto'])
items = WatchlistItem.all()
assert len(items) == 5
googl = WatchlistItem.get('GOOGL')
assert googl.willing_to_own == 0
print('cmd_add space-separated OK:', len(items), 'total')

# Test duplicate skip
cmd_add(['NVDA,TSLA'])
items = WatchlistItem.all()
assert len(items) == 6  # TSLA added, NVDA skipped
print('Duplicate skip OK: 6 total')

# Test list functions
opt = WatchlistItem.optionable()
assert len(opt) == 0  # none screened yet
print('optionable OK (0 screened)')

wto = WatchlistItem.willing_to_own_list()
assert len(wto) == 0  # none optionable + wto
print('willing_to_own_list OK')

# Mark one as optionable
nvda = WatchlistItem.get('NVDA')
nvda.mark_screened(True)
opt = WatchlistItem.optionable()
assert len(opt) == 1
print('mark_screened OK')

print()
print('ALL WATCHLIST TESTS PASSED')
