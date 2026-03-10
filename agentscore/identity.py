"""
Agent identity — secp256k1 keypair for on-chain attestation.

The private key signs attestation transactions. The derived address
is the agent's pseudonymous on-chain identity. No personal data is
linked to this key.

Stored at ~/.agentscore/agent.key (hex-encoded private key, chmod 600).
"""

import json
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

CONFIG_DIR = Path.home() / ".agentscore"
KEY_FILE = CONFIG_DIR / "agent.key"
CONFIG_FILE = CONFIG_DIR / "config.json"


def ensure_identity() -> Tuple[str, str]:
    """
    Ensure the agent has a keypair.  Generates one if missing.

    Returns:
        (private_key_hex, address)

    Raises:
        ImportError: if eth_account is not installed.
    """
    from eth_account import Account

    if KEY_FILE.exists():
        private_key = KEY_FILE.read_text().strip()
        account = Account.from_key(private_key)
        return private_key, account.address

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    private_key = "0x" + secrets.token_hex(32)
    account = Account.from_key(private_key)

    KEY_FILE.write_text(private_key)
    try:
        os.chmod(KEY_FILE, 0o600)
    except OSError as e:
        logger.warning(
            "[agentscore] Failed to set key file permissions (key may be world-readable): %s",
            e,
        )

    config = {
        "address": account.address,
        "created_at": time.time(),
    }
    CONFIG_FILE.write_text(json.dumps(config, indent=2))

    logger.info("Generated agent identity: %s", account.address)
    return private_key, account.address


def get_address() -> Optional[str]:
    """Get the agent's address if identity exists, else None."""
    if not KEY_FILE.exists():
        return None
    try:
        from eth_account import Account

        private_key = KEY_FILE.read_text().strip()
        return Account.from_key(private_key).address
    except ImportError:
        return None
