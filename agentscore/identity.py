"""
Agent identity — Coinbase CDP MPC wallet or local keypair fallback.

Priority:
  1. Coinbase CDP wallet (MPC, no private key on disk)
     Requires: pip install cdp-sdk
     Requires: CDP_API_KEY_ID + CDP_API_KEY_SECRET env vars
  2. Local secp256k1 keypair (fallback if CDP unavailable)

The derived address is the agent's pseudonymous on-chain identity.
No personal data is linked to this wallet.
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
CONFIG_FILE = CONFIG_DIR / "config.json"

# CDP wallet persistence
CDP_WALLET_FILE = CONFIG_DIR / "cdp_wallet.json"
CDP_SEED_FILE = CONFIG_DIR / "cdp_seed.json"

# Legacy local key
KEY_FILE = CONFIG_DIR / "agent.key"


def _ensure_cdp_wallet() -> Tuple[str, str]:
    """
    Create or restore a Coinbase CDP MPC wallet.

    Returns:
        (wallet_id, address)

    Raises:
        ImportError: if cdp-sdk is not installed.
        Exception: if CDP credentials are missing or wallet creation fails.
    """
    from cdp import Wallet, Cdp

    # Configure CDP from environment
    api_key_id = os.getenv("CDP_API_KEY_ID", "")
    api_key_secret = os.getenv("CDP_API_KEY_SECRET", "")

    if not api_key_id or not api_key_secret:
        raise EnvironmentError(
            "CDP_API_KEY_ID and CDP_API_KEY_SECRET must be set for Coinbase wallet"
        )

    Cdp.configure(
        api_key_id=api_key_id,
        api_key_secret=api_key_secret,
    )

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    # Try to restore existing wallet
    if CDP_WALLET_FILE.exists() and CDP_SEED_FILE.exists():
        try:
            wallet_data = json.loads(CDP_WALLET_FILE.read_text())
            wallet_id = wallet_data["wallet_id"]
            wallet = Wallet.fetch(wallet_id)
            wallet.load_seed(str(CDP_SEED_FILE))
            address = wallet.default_address.address
            logger.info(
                "[agentscore] Restored CDP wallet: %s (%s)", wallet_id[:12], address
            )
            return wallet_id, address
        except Exception as e:
            logger.warning("[agentscore] Failed to restore CDP wallet: %s", e)

    # Create new wallet
    wallet = Wallet.create()
    wallet_id = wallet.id
    address = wallet.default_address.address

    # Persist wallet ID and seed
    wallet.save_seed(str(CDP_SEED_FILE), encrypt=True)
    CDP_WALLET_FILE.write_text(json.dumps({
        "wallet_id": wallet_id,
        "address": address,
        "provider": "cdp",
        "created_at": time.time(),
    }, indent=2))

    # Restrict permissions on seed file
    try:
        os.chmod(CDP_SEED_FILE, 0o600)
        os.chmod(CDP_WALLET_FILE, 0o600)
    except OSError:
        pass

    logger.info(
        "[agentscore] Created CDP MPC wallet: %s (%s)", wallet_id[:12], address
    )
    return wallet_id, address


def _ensure_local_keypair() -> Tuple[str, str]:
    """
    Create or load a local secp256k1 keypair (fallback).

    Returns:
        (private_key_hex, address)
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
    except OSError:
        pass

    config = {
        "address": account.address,
        "provider": "local",
        "created_at": time.time(),
    }
    CONFIG_FILE.write_text(json.dumps(config, indent=2))

    logger.info("[agentscore] Generated local keypair: %s", account.address)
    return private_key, account.address


def ensure_identity() -> Tuple[str, str]:
    """
    Ensure the agent has a wallet. Tries Coinbase CDP first, falls back to local.

    Returns:
        (wallet_id_or_private_key, address)
    """
    # Try CDP MPC wallet first
    try:
        wallet_id, address = _ensure_cdp_wallet()
        return wallet_id, address
    except ImportError:
        logger.debug("[agentscore] cdp-sdk not installed, using local keypair")
    except EnvironmentError:
        logger.debug("[agentscore] CDP credentials not set, using local keypair")
    except Exception as e:
        logger.warning("[agentscore] CDP wallet failed (%s), falling back to local", e)

    # Fallback: local keypair
    try:
        return _ensure_local_keypair()
    except ImportError:
        pass

    # Last resort: generate address-only from config if it exists
    if CONFIG_FILE.exists():
        config = json.loads(CONFIG_FILE.read_text())
        addr = config.get("address")
        if addr:
            return "", addr

    if CDP_WALLET_FILE.exists():
        data = json.loads(CDP_WALLET_FILE.read_text())
        addr = data.get("address")
        if addr:
            return data.get("wallet_id", ""), addr

    raise RuntimeError(
        "Cannot create agent identity. Install cdp-sdk or eth_account."
    )


def get_address() -> Optional[str]:
    """Get the agent's address if identity exists, else None."""
    # Check CDP wallet first
    if CDP_WALLET_FILE.exists():
        try:
            data = json.loads(CDP_WALLET_FILE.read_text())
            return data.get("address")
        except Exception:
            pass

    # Check local config
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text())
            return data.get("address")
        except Exception:
            pass

    # Check legacy key file
    if KEY_FILE.exists():
        try:
            from eth_account import Account
            private_key = KEY_FILE.read_text().strip()
            return Account.from_key(private_key).address
        except (ImportError, Exception):
            pass

    return None


def get_wallet_provider() -> str:
    """Return which wallet provider is active: 'cdp', 'local', or 'none'."""
    if CDP_WALLET_FILE.exists():
        return "cdp"
    if KEY_FILE.exists() or CONFIG_FILE.exists():
        return "local"
    return "none"
