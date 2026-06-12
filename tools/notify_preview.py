import sys
sys.path.insert(0, '/Users/russmacbookpro/Projects/helm')
from helm.cli.notify import build_summary, format_notification
s = build_summary()
title, message, subtitle = format_notification(s)
print('Title:   ', title)
print('Subtitle:', subtitle)
print('Message: ', message)
print()
print('take_profit:', [r['ticker'] for r in s.get('take_profit',[])])
print('stop_loss:  ', [r['ticker'] for r in s.get('stop_loss',[])])
print('n:', s['n'], 'total_pnl:', round(s['total_pnl'],2))
