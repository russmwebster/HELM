# helm/config.py
# HELM configuration — paths, environment, active account
# All other modules import from here. No hardcoded paths elsewhere.

import os
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────────

# Project root: ~/Projects/helm
PROJECT_ROOT = Path(os.environ.get('HELM_ROOT', '~/Projects/helm')).expanduser()

# Data directory: where the database and any data files live
DATA_DIR = PROJECT_ROOT / 'data'
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Database
DB_PATH = Path(os.environ.get('HELM_DB', DATA_DIR / 'helm.db'))

# Schema and seed files
SCHEMA_PATH  = PROJECT_ROOT / 'helm' / 'schema.sql'
SEED_PATH    = PROJECT_ROOT / 'helm' / 'seed_defaults.sql'

# Logs
LOG_DIR = PROJECT_ROOT / 'logs'
LOG_DIR.mkdir(parents=True, exist_ok=True)

# ── Python environment ───────────────────────────────────────────────────────

HELM_PYTHON = '/opt/anaconda3/envs/helm/bin/python3'

# ── Schema version ───────────────────────────────────────────────────────────
# Bump this when schema.sql changes. db.py uses it to detect migrations needed.

SCHEMA_VERSION = '1.5'

# ── Runtime state ────────────────────────────────────────────────────────────
# These can be overridden by CLI flags or environment variables.

# Default account ID (set during setup, stored in ~/.helm_profile)
_PROFILE_PATH = Path.home() / '.helm_profile'

def get_active_account() -> str | None:
    """Return the active account ID from the profile file, or None if not set."""
    if _PROFILE_PATH.exists():
        return _PROFILE_PATH.read_text().strip() or None
    return None

def set_active_account(account_id: str) -> None:
    """Persist the active account ID to the profile file."""
    _PROFILE_PATH.write_text(account_id.strip())

# ── Display ──────────────────────────────────────────────────────────────────

APP_NAME    = 'HELM'
APP_VERSION = '0.1.0'
APP_TAGLINE = 'High-conviction Entry & Lifecycle Manager'
