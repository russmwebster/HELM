# helm/dates.py
# Low-level date helpers. Leaf module: no HELM imports, nothing cycles back here.
# `dte` relocated verbatim from helm/cli/check_cmd.py (HELM-027 WS4 prep) so that
# helm.decision can depend on it without reaching up into the CLI layer.

from datetime import date, datetime
from typing import Optional


def dte(expiration: str) -> Optional[int]:
    if not expiration:
        return None
    try:
        exp = datetime.strptime(expiration, "%Y-%m-%d").date()
        return (exp - date.today()).days
    except Exception:
        return None
