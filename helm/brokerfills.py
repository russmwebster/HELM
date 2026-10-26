"""W151 - typed reader for a Fidelity activity export.

READ-ONLY. This module parses; it never writes.

Why it exists: activity_cmd.find_matching_position matches on
ticker + expiration + strike + option type and reads neither direction
nor quantity (HELM-187). On a 30-day export that key is not merely weak,
it is ambiguous. In the frozen fixture LRCX 2026-08-21 P360 appears
three times - sold-to-open 20, bought-to-close 7, assigned 13. Same key,
three meanings, one of them a partial.

So every row here carries the action TYPED: event, leg direction and
open/close. Quantity is carried but is deliberately NOT part of the key -
match on quantity and a partial close can never be represented.

Two things the fixture exists to catch, both handled here:
  * the as-of date. An assignment can carry Run Date 08/20 and the text
    "as of 2026-08-19". The as-of date is the real one.
  * the direction inversion on a close. A BOUGHT CLOSING row closes a
    SHORT leg; a SOLD CLOSING row closes a LONG leg. leg_direction is
    the direction of the LEG, not of the trade.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import datetime

OPTION_SYMBOL = re.compile(r"^-([A-Z]+)(\d{2})(\d{2})(\d{2})([CP])(\d+(?:\.\d+)?)$")
AS_OF_ISO = re.compile(r"as of\s+(\d{4}-\d{2}-\d{2})", re.I)
AS_OF_US = re.compile(r"as of\s+(\d{2})-(\d{2})-(\d{2})", re.I)
SHARE_LABEL = re.compile(r"^YOU (.+?) AS OF", re.I)

EVENTS = ("OPEN", "CLOSE", "ASSIGNED", "EXERCISED", "EXPIRED", "SHARE")


@dataclass(frozen=True)
class Fill:
    """One row of the export, with its action typed rather than discarded."""

    row: int
    run_date: str
    as_of: str
    dated_from_as_of: bool
    action: str
    event: str
    is_option: bool
    symbol: str
    ticker: str
    option_type: str
    strike: float
    expiration: str
    leg_direction: str
    open_close: str
    qty: int
    price: float
    commission: float
    fees: float
    amount: float
    raw_action: str

    @property
    def contract(self):
        """Contract identity. NOT sufficient as a match key on its own."""
        return (self.ticker, self.expiration, self.option_type, self.strike)

    @property
    def key(self):
        """The widened key: contract + direction + open/close. No quantity."""
        return self.contract + (self.leg_direction, self.open_close)

    @property
    def gross_cash(self):
        """Signed cash before commission and fees, from price and quantity.

        A sale is a credit, so the sign is the opposite of the quantity's.
        Returns None when the row carries no price (expiry, assignment).
        """
        if self.price is None or not self.is_option:
            return None
        return round(-self.qty * self.price * 100.0, 2)


@dataclass
class Parsed:
    fills: list
    skipped: int
    fieldnames: list


def _num(value, default=None):
    if value is None:
        return default
    text = str(value).strip().replace(",", "").replace("$", "")
    if text in ("", "--", "None"):
        return default
    try:
        return float(text)
    except ValueError:
        return default


def _int(value, default=0):
    found = _num(value, None)
    return default if found is None else int(round(found))


def _date(value):
    text = (value or "").strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def _as_of(action, run_date):
    """The date that actually happened. Prefers the as-of text over Run Date."""
    hit = AS_OF_ISO.search(action)
    if hit:
        return hit.group(1), True
    hit = AS_OF_US.search(action)
    if hit:
        month, day, year = hit.groups()
        return "20" + year + "-" + month + "-" + day, True
    return run_date, False


def _parse_symbol(symbol):
    """-LRCX260821P360 -> (LRCX, 2026-08-21, PUT, 360.0). Shares -> ticker only."""
    text = (symbol or "").strip()
    hit = OPTION_SYMBOL.match(text)
    if not hit:
        return text, None, None, None, False
    ticker, yy, mm, dd, right, strike = hit.groups()
    expiration = "20" + yy + "-" + mm + "-" + dd
    return ticker, expiration, ("PUT" if right == "P" else "CALL"), float(strike), True


def _classify(raw_action, is_option):
    """(action label, event, leg_direction, open_close) from the Action text."""
    text = re.sub(r"\s+", " ", (raw_action or "")).strip().upper()

    if not is_option:
        hit = SHARE_LABEL.match(text)
        label = hit.group(1) if hit else " ".join(text.split()[1:4])
        return label, "SHARE", None, None

    # Order matters: the share forms above contain ASSIGNED and EXERCISED too,
    # which is why the option/share split is made on the SYMBOL, not the text.
    if text.startswith("EXPIRED"):
        return "EXPIRED", "EXPIRED", None, None
    if text.startswith("EXERCISED"):
        return "EXERCISED", "EXERCISED", None, None
    if text.startswith("ASSIGNED"):
        return "ASSIGNED", "ASSIGNED", None, None

    bought = "YOU BOUGHT" in text
    side = "BOUGHT" if bought else "SOLD"
    if "OPENING TRANSACTION" in text:
        return side + " OPENING", "OPEN", ("LONG" if bought else "SHORT"), "OPENING"
    if "CLOSING TRANSACTION" in text:
        # A bought close closes a SHORT leg; a sold close closes a LONG leg.
        return side + " CLOSING", "CLOSE", ("SHORT" if bought else "LONG"), "CLOSING"
    return text[:40], "SHARE", None, None


def parse_activity_text(raw, keep_account=None):
    """Parse the export. Returns Parsed(fills, skipped, fieldnames).

    s136: keep_account -- when the export carries an Account column (the
    multi-account Accounts_History format), rows for any other account name
    are skipped (counted in `skipped`). The single-account
    History_for_Account_<n> format has no such column; read_export() decides
    whether the whole file is the account.
    """
    body = raw.lstrip("\ufeff").lstrip("\r\n")
    reader = csv.DictReader(io.StringIO(body))
    fills = []
    skipped = 0
    for index, row in enumerate(reader):
        if keep_account is not None and "Account" in row and \
                (row.get("Account") or "").strip() != keep_account:
            skipped += 1
            continue
        run_date = _date(row.get("Run Date"))
        symbol = (row.get("Symbol") or "").strip()
        # Fidelity appends a multi-line disclaimer after the data. A row with
        # no parseable Run Date is not a transaction.
        if not run_date or not symbol:
            skipped += 1
            continue
        ticker, expiration, option_type, strike, is_option = _parse_symbol(symbol)
        raw_action = (row.get("Action") or "").strip()
        action, event, leg_direction, open_close = _classify(raw_action, is_option)
        as_of, from_as_of = _as_of(raw_action, run_date)
        fills.append(
            Fill(
                row=index,
                run_date=run_date,
                as_of=as_of,
                dated_from_as_of=from_as_of,
                action=action,
                event=event,
                is_option=is_option,
                symbol=symbol,
                ticker=ticker,
                option_type=option_type,
                strike=strike,
                expiration=expiration,
                leg_direction=leg_direction,
                open_close=open_close,
                qty=_int(row.get("Quantity")),
                price=_num(row.get("Price ($)")),
                commission=_num(row.get("Commission ($)"), 0.0),
                fees=_num(row.get("Fees ($)"), 0.0),
                amount=_num(row.get("Amount ($)"), 0.0),
                raw_action=re.sub(r"\s+", " ", raw_action),
            )
        )
    return Parsed(fills=fills, skipped=skipped, fieldnames=list(reader.fieldnames or []))


def parse_activity_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as handle:
        return parse_activity_text(handle.read())


# ---------------------------------------------------------------------------
# s136 (2026-10-26): the Fidelity import's reader -- both export formats, the
# account kept to the Rollover IRA, the account number never stored or shown,
# split fills merged, and a stable key per row so an overlapping download is
# never booked twice. Read-only, like everything above.
#
#   Accounts_History*.csv           many accounts; "Account" (name) and
#                                   "Account Number" columns on every row
#   History_for_Account_<n>*.csv    one account; the number is in the file
#                                   name, and no row names the account
#
# The account is identified by a SHA-256 of its number (helm_meta key
# ACCOUNT_HASH_KEY). HELM learns it from any download that pairs the number
# with the name "Rollover IRA" (Accounts_History, Portfolio_Positions).
# ---------------------------------------------------------------------------
import hashlib
import os

IRA_NAME = "Rollover IRA"
ACCOUNT_HASH_KEY = "fidelity_ira_account_sha256"
_HISTORY_FOR = re.compile(r"History_for_Account_(\d+)", re.I)


def account_hash(number):
    return hashlib.sha256(str(number).strip().encode()).hexdigest()


def export_format(path):
    name = os.path.basename(path)
    if _HISTORY_FOR.search(name):
        return "HISTORY_FOR_ACCOUNT"
    if name.lower().startswith("accounts_history"):
        return "ACCOUNTS_HISTORY"
    if name.lower().startswith("portfolio_positions"):
        return "PORTFOLIO_POSITIONS"
    return None


def learn_ira_hash(path):
    """The SHA-256 of the account number that a download labels "Rollover IRA",
    or None. Reads Accounts_History ("Account"/"Account Number") and
    Portfolio_Positions ("Account name"/"Account number"). Never returns the
    number itself."""
    fmt = export_format(path)
    if fmt not in ("ACCOUNTS_HISTORY", "PORTFOLIO_POSITIONS"):
        return None
    with open(path, newline="", encoding="utf-8-sig") as handle:
        body = handle.read().lstrip("\ufeff").lstrip("\r\n")
    found = set()
    for row in csv.DictReader(io.StringIO(body)):
        name = (row.get("Account") or row.get("Account name") or "").strip()
        num = (row.get("Account Number") or row.get("Account number") or "").strip()
        if name == IRA_NAME and num.isdigit():
            found.add(account_hash(num))
    return found.pop() if len(found) == 1 else None


@dataclass
class Export:
    path: str
    fmt: str
    fills: list          # Rollover IRA rows only
    keys: list           # one stable key per fill, same order
    skipped: int         # disclaimer lines, other accounts
    other_account: bool  # a History_for_Account file for some other account
    hash_unknown: bool   # a History_for_Account file and HELM has no IRA hash yet


def _fill_keys(fills):
    """A key per row from what the row says, plus its occurrence number among
    identical rows -- two real 1-lot fills at the same price on the same day
    stay two, and the same row in two overlapping downloads is one."""
    seen = {}
    keys = []
    for f in fills:
        base = "|".join(str(x) for x in (f.run_date, f.as_of, f.raw_action, f.symbol.strip(),
                                          f.qty, f.price, f.commission, f.fees, f.amount))
        n = seen.get(base, 0)
        seen[base] = n + 1
        keys.append(hashlib.sha256(("%s|#%d" % (base, n)).encode()).hexdigest()[:24])
    return keys


def read_export(path, ira_hash=None):
    """Read one Fidelity activity download, Rollover IRA rows only."""
    fmt = export_format(path)
    if fmt not in ("HISTORY_FOR_ACCOUNT", "ACCOUNTS_HISTORY"):
        raise ValueError("not a Fidelity activity download: %s" % os.path.basename(path))
    with open(path, newline="", encoding="utf-8-sig") as handle:
        raw = handle.read()
    if fmt == "ACCOUNTS_HISTORY":
        parsed = parse_activity_text(raw, keep_account=IRA_NAME)
        fills = parsed.fills
        return Export(path, fmt, fills, _fill_keys(fills), parsed.skipped, False, False)
    num = _HISTORY_FOR.search(os.path.basename(path)).group(1)
    if ira_hash is None:
        return Export(path, fmt, [], [], 0, False, True)
    if account_hash(num) != ira_hash:
        return Export(path, fmt, [], [], 0, True, False)
    parsed = parse_activity_text(raw)
    return Export(path, fmt, parsed.fills, _fill_keys(parsed.fills), parsed.skipped, False, False)


def merge_key(f):
    """Split fills of one order: same day, contract, leg direction, event."""
    return (f.event, f.as_of, f.ticker, f.expiration, f.option_type, f.strike, f.leg_direction)


def merge_option_fills(fills, keys):
    """Group option rows by merge_key. Returns {merge_key: dict} with total
    contracts, the contract-weighted price, commission, fees, amount and the
    row keys. Expiries and assignments merge the same way (price None)."""
    out = {}
    for f, k in zip(fills, keys):
        if not f.is_option:
            continue
        g = out.setdefault(merge_key(f), {"contracts": 0, "px_sum": 0.0, "priced": True,
                                          "commission": 0.0, "fees": 0.0, "amount": 0.0,
                                          "rows": [], "keys": [], "symbol": f.symbol.strip()})
        q = abs(f.qty)
        g["contracts"] += q
        if f.price is None:
            g["priced"] = False
        else:
            g["px_sum"] += q * f.price
        g["commission"] += f.commission or 0.0
        g["fees"] += f.fees or 0.0
        g["amount"] += f.amount or 0.0
        g["rows"].append(f)
        g["keys"].append(k)
    for g in out.values():
        g["price"] = (g["px_sum"] / g["contracts"]) if (g["priced"] and g["contracts"]) else None
    return out
