def evaluate_debit_spreads(ticker, strategy, config, dte_target=None, top_n=5):
    import yfinance as yf
    from datetime import date, datetime
    is_bear = strategy == 'BEAR_PUT_SPREAD'
    tk = yf.Ticker(ticker)
    spot = tk.fast_info.get('last_price') or tk.fast_info.get('previous_close', 0)
    if not spot: return []
    dte_min = config.get('dte_min', 30)
    dte_max = config.get('dte_max', 90)
    dte_sweet = config.get('dte_sweet', 60)
    widths = config.get('spread_widths', [5, 10, 15, 20, 25])
    today = date.today()
    results = []
    for exp in tk.options:
        d = datetime.strptime(exp, '%Y-%m-%d').date()
        dte = (d - today).days
        if not (dte_min <= dte <= dte_max): continue
        if dte_target and abs(dte - dte_target) > 10: continue
        try:
            chain = tk.option_chain(exp)
            opts = chain.puts if is_bear else chain.calls
        except Exception:
            continue
        opts = opts[opts['bid'] > 0].copy()
        if opts.empty: continue
        for _, long_row in opts.iterrows():
            long_strike = long_row['strike']
            sp = long_strike / spot
            if is_bear:
                if not (0.88 <= sp <= 1.02): continue
            else:
                if not (0.98 <= sp <= 1.12): continue
            long_mid = (long_row['bid'] + long_row['ask']) / 2
            long_oi = int(long_row.get('openInterest', 0) or 0)
            if long_oi < 500 or long_mid <= 0: continue
            for width in widths:
                short_strike = long_strike - width if is_bear else long_strike + width
                sm = opts[opts['strike'] == short_strike]
                if sm.empty: continue
                short_row = sm.iloc[0]
                short_mid = (short_row['bid'] + short_row['ask']) / 2
                short_oi = int(short_row.get('openInterest', 0) or 0)
                if short_oi < 500 or short_mid <= 0: continue
                net_debit = round(long_mid - short_mid, 2)
                if net_debit <= 0: continue
                max_profit = round(width - net_debit, 2)
                if max_profit <= 0: continue
                dtw = round(net_debit / width * 100, 1)
                rr = round(max_profit / net_debit, 2)
                lsp = (long_row['ask'] - long_row['bid']) / long_mid * 100
                ssp = (short_row['ask'] - short_row['bid']) / short_mid * 100
                if max(lsp, ssp) > 30: continue
                score = 0.0
                if dtw <= 40: score += 20
                elif dtw <= 50: score += 12
                else: score += 5
                if rr >= 1.5: score += 20
                elif rr >= 1.0: score += 12
                if min(long_oi, short_oi) >= 1000: score += 15
                elif min(long_oi, short_oi) >= 500: score += 8
                score -= abs(dte - dte_sweet) * 0.2
                results.append({'exp': exp, 'dte': dte, 'long_strike': long_strike,
                    'short_strike': short_strike, 'width': width, 'long_mid': long_mid,
                    'short_mid': short_mid, 'net_debit': net_debit, 'max_profit': max_profit,
                    'debit_to_width_pct': dtw, 'rr': rr,
                    'long_oi': long_oi, 'short_oi': short_oi, 'score': round(score, 1)})
    return sorted(results, key=lambda x: -x['score'])[:top_n]