"""Print a 32-byte (256-bit) master key as 64 hex chars for KAIROS_CREDENTIAL_MASTER_KEY.

Usage: python scripts/generate_master_key.py
Copy the output into .env. Never commit it.
"""
from __future__ import annotations

import secrets

if __name__ == "__main__":
    print(secrets.token_hex(32))
