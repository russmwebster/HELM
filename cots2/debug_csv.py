import sys, pandas as pd
from pathlib import Path
CSV_PATH = str(Path.home() / 'Projects' / 'helm' / 'data' / 'portfolio_test.csv')

# Read with index_col=0 — the account number is the row index
df = pd.read_csv(CSV_PATH, index_col=0)
print('COLUMNS with index_col=0:', list(df.columns))
print('SHAPE:', df.shape)
print()
print('ROW 0:')
for col, val in zip(df.columns, df.iloc[0]):
    print(f'  {repr(col)}: {repr(val)}')
print()
print('ROW 2 (first option):')
print('  INDEX:', df.index[2])
for col, val in zip(df.columns, df.iloc[2]):
    print(f'  {repr(col)}: {repr(val)}')
