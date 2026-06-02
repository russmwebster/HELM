def display_straddles(ticker, strategy, config, straddles, spot, atr, account_id, args):
    from rich.table import Table
    from rich import box as _box
    console.print()
    console.print(Panel.fit(
        f'[bold]HELM Open -- {ticker} Long Straddle[/bold]\n'
        f'[dim]Buy ATM call + put | DTE {config["dte_min"]}-{config["dte_max"]} | Data: IBKR live[/dim]',
        border_style='cyan'))
    console.print()
    try:
        from helm.models.iv_history import IVHistory
        ivr_data = IVHistory.for_tickers([ticker]).get(ticker, {})
        ivr_val = ivr_data.get('iv_rank') if ivr_data else None
        if ivr_val is not None:
            if ivr_val > 40:
                console.print(f'  [yellow]Warning IVR {ivr_val:.0f} -- elevated. Best at IVR < 35.[/yellow]')
            else:
                console.print(f'  [green]IVR {ivr_val:.0f} -- cheap. Good straddle entry.[/green]')
            console.print()
    except Exception:
        pass
    if atr:
        console.print(f'  Spot: ${spot:,.2f}  ATR(14): ${atr:.2f}')
        console.print()
    tbl = Table(box=_box.SIMPLE, show_header=True, header_style='bold dim')
    for col, w in [('Rank',5),('Exp',10),('DTE',5),('Strike',8),('Call Mid',9),('Put Mid',9),('Total Cost',11),('Min OI',8),('Break-evens',22),('Move Needed',12),('Score',7)]:
        tbl.add_column(col, justify='right' if col not in ('Rank','Exp','Break-evens') else 'left', width=w)
    for i, s in enumerate(straddles, 1):
        tbl.add_row(f'#{i}', s['exp'], str(s['dte']), f'${s["strike"]:.1f}',
            f'${s["call_mid"]:.2f}', f'${s["put_mid"]:.2f}', f'${s["total_debit"]:.2f}',
            f'{min(s["call_oi"],s["put_oi"]):,}', f'${s["be_down"]:.2f} / ${s["be_up"]:.2f}',
            f'{s["pct_move_needed"]:.1f}%', str(s['score']))
    console.print(f'Top {len(straddles)} straddles -- {ticker} Long Straddle')
    console.print()
    console.print(tbl)
    console.print()
    best = straddles[0]
    contracts = suggest_contracts(strategy, best['strike'], best['total_debit'], account_id, ticker=ticker)
    total_cost = round(best['total_debit'] * contracts * 100, 2)
    console.print(Panel(
        f'[bold green]Top pick:[/bold green] {ticker} Straddle ${best["strike"]:.1f} {best["exp"]} ({best["dte"]}d)\n'
        f'  Buy CALL ${best["strike"]:.1f} @ ${best["call_mid"]:.2f}  |  Buy PUT ${best["strike"]:.1f} @ ${best["put_mid"]:.2f}\n'
        f'  Total debit: ${best["total_debit"]:.2f}/contract  |  Break-evens: ${best["be_down"]:.2f} / ${best["be_up"]:.2f}\n'
        f'  Move needed: {best["pct_move_needed"]:.1f}% in either direction\n\n'
        f'  Suggested: {contracts} contract(s)  |  Total cost: ${total_cost:,.0f}\n\n'
        f'[dim]To open: [bold]helm open {ticker} LONG_STRADDLE --confirm[/bold][/dim]',
        title='Recommendation', border_style='green'))
    console.print()