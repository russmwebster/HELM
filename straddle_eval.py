def evaluate_straddles(ticker, strategy, config, dte_target=None, top_n=5):
    import yfinance as yf
    from datetime import date, datetime
    tk = yf.Ticker(ticker)
    spot = tk.fast_info.get('last_price') or tk.fast_info.get('previous_close', 0)
    if not spot: return []
    dte_min = config.get('dte_min', 30)
    dte_max = config.get('dte_max', 90)
    dte_sweet = config.get('dte_sweet', 45)
    today = date.today()
    results = []
    for exp in tk.options:
        d = datetime.strptime(exp, '%Y-%m-%d').date()
        dte = (d - today).days
        if not (dte_min <= dte <= dte_max): continue
        if dte_target and abs(dte - dte_target) > 10: continue
        try:
            chain = tk.option_chain(exp)
        except Exception:
            continue
        calls = chain.calls[chain.calls['bid'] > 0].copy()
        puts  = chain.puts[chain.puts['bid'] > 0].copy()
        if calls.empty or puts.empty: continue
        calls['spot_dist'] = abs(calls['strike'] - spot)
        for _, call_row in calls.nsmallest(3, 'spot_dist').iterrows():
            strike = call_row['strike']
            pm = puts[puts['strike'] == strike]
            if pm.empty: continue
            put_row = pm.iloc[0]
            call_mid = (call_row['bid'] + call_row['ask']) / 2
            put_mid  = (put_row['bid']  + put_row['ask'])  / 2
            total_debit = round(call_mid + put_mid, 2)
            call_oi = int(call_row.get('openInterest', 0) or 0)
            put_oi  = int(put_row.get('openInterest', 0)  or 0)
            if min(call_oi, put_oi) < 500: continue
            call_sp = (call_row['ask'] - call_row['bid']) / call_mid * 100 if call_mid > 0 else 99
            put_sp  = (put_row['ask']  - put_row['bid'])  / put_mid  * 100 if put_mid  > 0 else 99
            if max(call_sp, put_sp) > 25: continue
            score = 50.0 - abs(strike - spot) / spot * 50
            score += max(0, 10 - max(call_sp, put_sp)) - abs(dte - dte_sweet) * 0.2
            score += min(10, min(call_oi, put_oi) / 500)
            results.append({'exp': exp, 'dte': dte, 'strike': strike,
                'call_mid': call_mid, 'put_mid': put_mid, 'total_debit': total_debit,
                'call_oi': call_oi, 'put_oi': put_oi,
                'be_down': round(strike - total_debit, 2),
                'be_up':   round(strike + total_debit, 2),
                'pct_move_needed': round(total_debit / spot * 100, 1),
                'score': round(score, 1)})
    return sorted(results, key=lambda x: -x['score'])[:top_n]