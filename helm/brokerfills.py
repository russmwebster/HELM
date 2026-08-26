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


def parse_activity_text(raw):
    """Parse the export. Returns Parsed(fills, skipped, fieldnames)."""
    body = raw.lstrip("﻿").lstrip("\r\n")
    reader = csv.DictReader(io.StringIO(body))
    fills = []
    skipped = 0
    for index, row in enumerate(reader):
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
