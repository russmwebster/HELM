# helm/secrets_loader.py
# Dependency-free loader for repo-root .env secrets. Injects KEY=VALUE lines
# into os.environ WITHOUT overriding values already present (an explicit shell
# export or launchd env var wins). Idempotent.

import os
from pathlib import Path

_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def load_env(path: Path = _ENV_PATH) -> None:
    """Load KEY=VALUE pairs from .env into os.environ if not already set."""
    try:
        text = path.read_text()
    except FileNotFoundError:
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val
