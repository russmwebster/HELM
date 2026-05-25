
# helm/ibkr.py
# HELM IBKR Connection Manager
# Handles connect, disconnect, and connection checking for IB Gateway.
#
# Usage:
#   from helm.ibkr import get_ib, disconnect_ib, check_connection
#
# IB Gateway ports:
#   Live trading:   4001 (TWS) or 4002 (Gateway)
#   Paper trading:  4002 (TWS) or 4003 (Gateway)
#
# ClientId: 10 (HELM default -- avoids conflict with COTS which uses 1)

import logging
import time
from typing import Optional

logging.getLogger("ib_insync").setLevel(logging.CRITICAL)
logging.getLogger("asyncio").setLevel(logging.CRITICAL)

# ── Default connection settings ───────────────────────────────
IBKR_HOST      = "127.0.0.1"
IBKR_PORT      = 4002   # IB Gateway live (change to 4003 for paper)
IBKR_CLIENT_ID = 10     # HELM client ID -- avoids conflict with COTS (uses 1)
IBKR_TIMEOUT   = 10     # seconds

# Module-level connection cache
_ib = None


def get_ib(host: str = IBKR_HOST,
           port: int = IBKR_PORT,
           client_id: int = IBKR_CLIENT_ID,
           timeout: int = IBKR_TIMEOUT,
           readonly: bool = True):
    """
    Return a connected IB instance, reusing existing connection if live.
    readonly=True: data only, no order submission.
    """
    global _ib
    from ib_insync import IB

    if _ib is not None and _ib.isConnected():
        return _ib

    _ib = IB()
    try:
        _ib.connect(host, port, clientId=client_id,
                    timeout=timeout, readonly=readonly)
        return _ib
    except Exception as e:
        _ib = None
        raise ConnectionError(f"Cannot connect to IB Gateway at {host}:{port} -- {e}")


def disconnect_ib():
    """Cleanly disconnect from IB Gateway."""
    global _ib
    if _ib is not None:
        try:
            _ib.disconnect()
        except Exception:
            pass
        _ib = None


def is_connected() -> bool:
    """Return True if currently connected to IB Gateway."""
    global _ib
    return _ib is not None and _ib.isConnected()


def check_connection(host: str = IBKR_HOST,
                     port: int = IBKR_PORT,
                     client_id: int = IBKR_CLIENT_ID,
                     timeout: int = 5) -> dict:
    """
    Probe the IB Gateway and return a status dict.
    Does NOT cache the connection -- probe only.
    Returns:
        {
            "connected": bool,
            "host": str,
            "port": int,
            "client_id": int,
            "accounts": list,
            "error": str or None
        }
    """
    from ib_insync import IB
    result = {
        "connected": False,
        "host": host,
        "port": port,
        "client_id": client_id,
        "accounts": [],
        "error": None,
    }
    ib = IB()
    try:
        ib.connect(host, port, clientId=client_id, timeout=timeout, readonly=True)
        result["connected"] = True
        result["accounts"] = list(ib.managedAccounts())
    except Exception as e:
        result["error"] = str(e)
    finally:
        try:
            ib.disconnect()
        except Exception:
            pass
    return result


def free_client_id(host: str = IBKR_HOST,
                   port: int = IBKR_PORT,
                   client_id: int = IBKR_CLIENT_ID,
                   timeout: int = 5) -> bool:
    """
    Check if a clientId slot is occupied (hogged by a stale connection).
    Returns True if the slot is FREE, False if occupied.
    """
    from ib_insync import IB
    ib = IB()
    try:
        ib.connect(host, port, clientId=client_id, timeout=timeout, readonly=True)
        ib.disconnect()
        return True  # connected cleanly = slot is free
    except Exception as e:
        err = str(e).lower()
        if "already in use" in err or "clientid" in err:
            return False  # slot is occupied
        return True  # other error -- gateway may be down
    finally:
        try:
            ib.disconnect()
        except Exception:
            pass


def kick_client_id(host: str = IBKR_HOST,
                   port: int = IBKR_PORT,
                   timeout: int = 5) -> bool:
    """
    Attempt to free a hogged clientId by connecting as a temporary client (99),
    sending reqGlobalCancel, and disconnecting cleanly.
    Returns True if successful.
    """
    from ib_insync import IB
    ib = IB()
    try:
        ib.connect(host, port, clientId=99, timeout=timeout)
        ib.client.reqGlobalCancel()
        time.sleep(1)
        ib.disconnect()
        time.sleep(1)
        return True
    except Exception:
        return False
    finally:
        try:
            ib.disconnect()
        except Exception:
            pass
