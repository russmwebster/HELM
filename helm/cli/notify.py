"""
helm/cli/notify.py
Send macOS notification with portfolio summary.
Syncs to iPhone via iCloud Notification Center.

Usage:
  helm notify          Send portfolio summary notification now
  helm notify test     Send a test notification
"""

import sys
import subprocess
from datetime import datetime
from helm.db import get_conn


TRIGGER_FILE = '/tmp/helm_notify_trigger.txt'
NOTIFIER    = '/opt/homebrew/bin/terminal-notifier'

def send_notification(title: str, message: str, subtitle: str = "") -> bool:
    """
    Send notification via two methods:
    1. Write trigger file (picked up by launchd WatchPaths agent with GUI access)
    2. Try direct terminal-notifier (works when called from user terminal session)
    """
    import json
    payload = json.dumps({'title': title, 'message': message, 'subtitle': subtitle})
    # Write trigger file for the GUI watcher agent
    try:
        with open(TRIGGER_FILE, 'w') as f:
            f.write(payload)
    except Exception:
        pass
    # Also try direct (works in terminal session)
    try:
        cmd = [NOTIFIER, '-title', title, '-message', message, '-sound', 'default']
        if subtitle:
            cmd += ['-subtitle', subtitle]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return result.returncode == 0
    except Exception:
        return True  # trigger file written, watcher will handle it


def build_summary() -> dict:
    """Query DB for current portfolio summary."""
    conn = get_conn()

    # Get latest check per open position
    rows = conn.execute("""
        SELECT p.ticker, p.strategy, p.id,
               p.net_premium,
               c.health_flag, c.action_signal,
               c.pnl_unrealized, c.dte_now, c.iv_rank
        FROM positions p
        JOIN checks c ON c.position_id = p.id
        WHERE p.status = 'OPEN'
          AND c.data_quality = 'GOOD'
          AND c.checked_at = (
              SELECT MAX(c2.checked_at) FROM checks c2
              WHERE c2.position_id = p.id
                AND c2.data_quality = 'GOOD'
          )
        ORDER BY c.health_flag DESC, c.pnl_unrealized ASC
    """).fetchall()

    if not rows:
        return {'n': 0, 'total_pnl': 0, 'reds': [], 'yellows': [], 'greens': []}

    total_pnl = sum(float(r['pnl_unrealized'] or 0) for r in rows)
    reds    = [r for r in rows if r['health_flag'] == 'RED']
    yellows = [r for r in rows if r['health_flag'] == 'YELLOW']
    greens  = [r for r in rows if r['health_flag'] == 'GREEN']

    # Expiring soon (<=7 DTE)
    expiring = [r for r in rows if r['dte_now'] and r['dte_now'] <= 7]

    take_profit = []
    stop_loss   = []
    for r in rows:
        pnl  = float(r['pnl_unrealized'] or 0)
        prem = abs(float(r['net_premium'] or 0))
        if prem > 0:
            if pnl >= prem * 0.50:
                take_profit.append(r)
            elif pnl <= -prem * 2.0:
                stop_loss.append(r)
    return {
        'n':           len(rows),
        'total_pnl':   total_pnl,
        'reds':        reds,
        'yellows':     yellows,
        'greens':      greens,
        'expiring':    expiring,
        'take_profit': take_profit,
        'stop_loss':   stop_loss,
    }


def format_notification(summary: dict) -> tuple:
    """Format title and message for the notification."""
    if summary['n'] == 0:
        return "HELM", "No open positions.", ""

    pnl = summary['total_pnl']
    pnl_str = f"+${pnl:,.0f}" if pnl >= 0 else f"-${abs(pnl):,.0f}"

    n_red    = len(summary['reds'])
    n_yellow = len(summary['yellows'])
    n_green  = len(summary['greens'])
    n_exp    = len(summary['expiring'])

    tp_list = summary.get('take_profit', [])
    sl_list = summary.get('stop_loss', [])
    n_tp = len(tp_list)
    n_sl = len(sl_list)
    need_action = n_tp + n_sl + n_red

    # Title: portfolio snapshot
    title = f"HELM  {pnl_str}  |  {summary['n']} positions"

    # Subtitle: action flags + health
    parts = []
    if n_tp:     parts.append(f"TAKE PROFIT: {n_tp}")
    if n_sl:     parts.append(f"STOP LOSS: {n_sl}")
    if n_red:    parts.append(f"{n_red} RED")
    if n_yellow: parts.append(f"{n_yellow} YELLOW")
    if not parts: parts.append(f"{n_green} GREEN")
    subtitle = "  |  ".join(parts)

    # Message: actionable items
    lines = []

    if n_tp:
        tickers = ", ".join(r['ticker'] for r in tp_list)
        lines.append(f"TAKE PROFIT: {tickers}")
    if n_sl:
        tickers = ", ".join(r['ticker'] for r in sl_list)
        lines.append(f"STOP LOSS: {tickers}")
    if n_exp:
        tickers = ", ".join(r['ticker'] for r in summary['expiring'])
        lines.append(f"Expiring ≤7d: {tickers}")

    if summary['reds']:
        # Show up to 3 red positions needing attention
        urgent = [r for r in summary['reds'] if r['action_signal'] == 'CLOSE'][:3]
        if urgent:
            tickers = ", ".join(r['ticker'] for r in urgent)
            lines.append(f"Review: {tickers}")

    if not lines:
        if n_green == summary['n']:
            lines.append("All positions healthy.")
        else:
            lines.append(f"{n_yellow} positions to monitor.")

    message = "  |  ".join(lines)

    return title, message, subtitle


def cmd_notify(args):
    """Send portfolio summary notification."""
    if 'test' in args:
        ok = send_notification(
            "HELM Test",
            "Notifications are working correctly.",
            "Portfolio monitor active"
        )
        if ok:
            print("✓ Test notification sent.")
        else:
            print("✗ Notification failed — check macOS notification permissions.")
        return

    summary = build_summary()
    title, message, subtitle = format_notification(summary)
    ok = send_notification(title, message, subtitle)

    if ok:
        n = summary['n']
        pnl = summary['total_pnl']
        print(f"✓ Notification sent — {n} positions, P&L: {'+'if pnl>=0 else ''}${pnl:,.0f}")
    else:
        print("✗ Notification failed.")


def run():
    args = sys.argv[1:]

    if args and args[0] in ('-h', '--help'):
        print("\nUsage:  helm notify [test]\n")
        print("  Send portfolio summary to macOS Notification Center (syncs to iPhone).")
        print("  test    Send a test notification to verify setup\n")
        return

    cmd_notify(args)


if __name__ == '__main__':
    run()
