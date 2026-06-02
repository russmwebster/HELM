def display_debit_spreads(ticker, strategy, config, spreads, spot, atr, account_id, args):
    from rich.table import Table
    from rich import box as _box
    is_bear  = strategy == 'BEAR_PUT_SPREAD'
    label    = config.get('label', strategy)
    leg_type = 'PUT' if is_bear else 'CALL'
    console.print()
    console.print(Panel.fit(
        f'[bold]HELM Open -- {ticker} {label}[/bold]\n'
        f'[dim]Debit spread | DTE {config["dte_min"]}-{config["dte_max"]} | Data: IBKR live[/dim]',
        border_style='cyan'))
    console.print()
    if atr:
        s1 = round(spot - atr, 2)
        s2 = round(spot - 2*atr, 2)
        console.print(f'  Spot: ${spot:,.2f}  ATR(14): ${atr:.2f}  -- 1-ATR: ${s1:,.2f}  2-ATR: ${s2:,.2f}')
        console.print()
    tbl = Table(box=_box.SIMPLE, show_header=True, header_style='bold dim')
    for col, w, just in [('Rank',5,'left'),('Exp',10,'left'),('DTE',5,'right'),
        ('Long',8,'right'),('Short',8,'right'),('Width',6,'right'),
        ('Debit',8,'right'),('Max Profit',10,'right'),('D/W%',6,'right'),
        ('R/R',6,'right'),('OI',7,'right'),('Score',7,'right')]:
        tbl.add_column(col, justify=just, width=w)
    for i, s in enumerate(spreads, 1):
        tbl.add_row(f'#{i}', s['exp'], str(s['dte']),
            f'${s["long_strike"]:.0f}', f'${s["short_strike"]:.0f}', f'${s["width"]}',
            f'${s["net_debit"]:.2f}', f'${s["max_profit"]:.2f}',
            f'{s["debit_to_width_pct"]:.0f}%', str(s['rr']),
            f'{min(s["long_oi"],s["short_oi"]):,}', str(s['score']))
    console.print(f'Top {len(spreads)} spreads -- {ticker} {label}')
    console.print()
    console.print(tbl)
    console.print()
    best = spreads[0]
    contracts = suggest_contracts(strategy, best['long_strike'], best['net_debit'], account_id, ticker=ticker)
    total_cost = round(best['net_debit'] * contracts * 100, 2)
    console.print(Panel(
        f'[bold green]Top pick:[/bold green] {ticker} {label} '
        f'${best["long_strike"]:.0f}/${best["short_strike"]:.0f} {best["exp"]} ({best["dte"]}d)\n'
        f'  Buy  {leg_type} ${best["long_strike"]:.0f} @ ${best["long_mid"]:.2f}  |  '
        f'Sell {leg_type} ${best["short_strike"]:.0f} @ ${best["short_mid"]:.2f}\n'
        f'  Net debit: ${best["net_debit"]:.2f}/contract  |  '
        f'Max profit: ${best["max_profit"]:.2f}/contract  |  Width: ${best["width"]}\n'
        f'  Debit/width: {best["debit_to_width_pct"]:.0f}%  |  R/R: {best["rr"]}\n\n'
        f'  Suggested: {contracts} contract(s)  |  Total cost: ${total_cost:,.0f}\n\n'
        f'[dim]To open: [bold]helm open {ticker} {strategy} --confirm[/bold][/dim]',
        title='Recommendation', border_style='green'))
    console.print()