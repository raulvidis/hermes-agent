"""
On-chain attestation client for the AgentScore contract on Base.

Submits behavioral attestations and queries scores.  All transactions
are signed with the agent's local keypair.

Privacy:  Only pseudonymous address + aggregate numbers go on-chain.
No user IDs, session content, or platform identifiers are included.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

NETWORKS = {
    "base-sepolia": {
        "rpc": "https://sepolia.base.org",
        "chain_id": 84532,
        "explorer": "https://sepolia.basescan.org",
    },
    "base": {
        "rpc": "https://mainnet.base.org",
        "chain_id": 8453,
        "explorer": "https://basescan.org",
    },
}

# Minimal ABI — only the functions we call.
CONTRACT_ABI = [
    {
        "inputs": [],
        "name": "register",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {"name": "toolCalls", "type": "uint16"},
            {"name": "toolErrors", "type": "uint16"},
            {"name": "duration", "type": "uint32"},
            {"name": "completed", "type": "bool"},
            {"name": "modelHash", "type": "bytes32"},
            {"name": "inputTokens", "type": "uint32"},
            {"name": "outputTokens", "type": "uint32"},
            {"name": "metricsHash", "type": "bytes32"},
        ],
        "name": "attest",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {
                "components": [
                    {"name": "toolCalls", "type": "uint16"},
                    {"name": "toolErrors", "type": "uint16"},
                    {"name": "duration", "type": "uint32"},
                    {"name": "completed", "type": "bool"},
                    {"name": "modelHash", "type": "bytes32"},
                    {"name": "inputTokens", "type": "uint32"},
                    {"name": "outputTokens", "type": "uint32"},
                    {"name": "metricsHash", "type": "bytes32"},
                ],
                "name": "attestations",
                "type": "tuple[]",
            }
        ],
        "name": "attestBatch",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {"name": "agent", "type": "address"},
            {"name": "window", "type": "uint256"},
        ],
        "name": "getScore",
        "outputs": [
            {"name": "score", "type": "uint256"},
            {"name": "sessions", "type": "uint256"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"name": "agent", "type": "address"}],
        "name": "getAttestationCount",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"name": "", "type": "address"}],
        "name": "registered",
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "view",
        "type": "function",
    },
]

CONFIG_DIR = Path.home() / ".agentscore"
DEPLOY_FILE = CONFIG_DIR / "contract.json"
PENDING_FILE = CONFIG_DIR / "pending.json"


class AgentScoreChain:
    """Client for the AgentScore contract on Base."""

    def __init__(self, network: str = "base-sepolia"):
        from web3 import Web3
        from eth_account import Account

        from .identity import ensure_identity

        net = NETWORKS.get(network)
        if not net:
            raise ValueError(f"Unknown network: {network}")

        self.network = network
        self.w3 = Web3(Web3.HTTPProvider(net["rpc"]))
        self.chain_id = net["chain_id"]
        self.explorer = net["explorer"]

        self.private_key, self.address = ensure_identity()
        self.account = Account.from_key(self.private_key)

        self.contract_address = self._load_contract_address()
        self.contract = None
        if self.contract_address:
            self.contract = self.w3.eth.contract(
                address=self.contract_address,
                abi=CONTRACT_ABI,
            )

    def _load_contract_address(self) -> Optional[str]:
        """Load contract address from config or env."""
        from web3 import Web3

        addr = os.getenv("AGENTSCORE_CONTRACT")
        if addr:
            return Web3.to_checksum_address(addr)

        if DEPLOY_FILE.exists():
            data = json.loads(DEPLOY_FILE.read_text())
            addr = data.get(self.network, {}).get("address")
            if addr:
                return Web3.to_checksum_address(addr)
        return None

    def is_ready(self) -> bool:
        """Check if chain client is connected and contract is set."""
        return self.w3.is_connected() and self.contract is not None

    def is_registered(self) -> bool:
        if not self.contract:
            return False
        return self.contract.functions.registered(self.address).call()

    def register(self) -> str:
        """Register the agent on-chain.  Returns tx hash."""
        tx = self.contract.functions.register().build_transaction(self._tx_params())
        return self._sign_and_send(tx)

    def attest(
        self,
        tool_calls: int,
        tool_errors: int,
        duration: int,
        completed: bool,
        model_hash: bytes,
        input_tokens: int,
        output_tokens: int,
        metrics_hash: bytes,
    ) -> str:
        """
        Submit a behavioral attestation.  Returns tx hash.

        Args:
            tool_calls:    Number of tool calls in the interaction.
            tool_errors:   Number of errored tool calls.
            duration:      Wall-clock seconds.
            completed:     Whether the interaction completed.
            model_hash:    keccak256 of the model identifier string.
            input_tokens:  Total input/prompt tokens consumed.
            output_tokens: Total output/completion tokens consumed.
            metrics_hash:  keccak256 of the private metrics payload.
        """
        tx = self.contract.functions.attest(
            min(tool_calls, 65535),
            min(tool_errors, 65535),
            min(duration, 2**32 - 1),
            completed,
            model_hash,
            min(input_tokens, 2**32 - 1),
            min(output_tokens, 2**32 - 1),
            metrics_hash,
        ).build_transaction(self._tx_params())
        return self._sign_and_send(tx)

    def attest_batch(self, attestations: List[Dict[str, Any]]) -> str:
        """
        Submit multiple behavioral attestations in a single transaction.

        Args:
            attestations: List of attestation dicts with keys:
                - tool_calls: int
                - tool_errors: int
                - duration: int
                - completed: bool
                - model_hash: bytes
                - input_tokens: int
                - output_tokens: int
                - metrics_hash: bytes

        Returns tx hash.
        """
        formatted = []
        for att in attestations:
            formatted.append(
                (
                    min(att["tool_calls"], 65535),
                    min(att["tool_errors"], 65535),
                    min(att["duration"], 2**32 - 1),
                    att["completed"],
                    att["model_hash"],
                    min(att["input_tokens"], 2**32 - 1),
                    min(att["output_tokens"], 2**32 - 1),
                    att["metrics_hash"],
                )
            )

        tx = self.contract.functions.attestBatch(formatted).build_transaction(
            self._tx_params()
        )
        return self._sign_and_send(tx)

    def get_score(self, agent: str = None, window: int = 0) -> Dict[str, int]:
        """Query an agent's on-chain score."""
        from web3 import Web3

        agent = Web3.to_checksum_address(agent or self.address)
        score, sessions = self.contract.functions.getScore(agent, window).call()
        return {"score": score, "sessions": sessions}

    def get_attestation_count(self, agent: str = None) -> int:
        from web3 import Web3

        agent = Web3.to_checksum_address(agent or self.address)
        return self.contract.functions.getAttestationCount(agent).call()

    def _tx_params(self) -> dict:
        return {
            "from": self.address,
            "nonce": self.w3.eth.get_transaction_count(self.address),
            "chainId": self.chain_id,
            "maxFeePerGas": self.w3.eth.gas_price * 2,
            "maxPriorityFeePerGas": self.w3.to_wei(0.001, "gwei"),
        }

    def _sign_and_send(self, tx: dict) -> str:
        signed = self.w3.eth.account.sign_transaction(tx, private_key=self.private_key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)
        return receipt.transactionHash.hex()


def _get_hmac_key() -> bytes:
    """Derive HMAC key from the agent's private key.

    Uses the agent key so only this agent can produce valid pending
    attestation files.  If the key file doesn't exist yet, returns a
    fallback — pending attestations saved before identity creation
    will be discarded on next load (acceptable trade-off).
    """
    import hashlib

    from .identity import KEY_FILE

    if KEY_FILE.exists():
        raw = KEY_FILE.read_text().strip().encode()
        return hashlib.sha256(b"agentscore-pending-hmac:" + raw).digest()
    return b"agentscore-no-key-fallback"


def _compute_hmac(data: str, key: bytes) -> str:
    import hashlib
    import hmac as _hmac

    return _hmac.new(key, data.encode(), hashlib.sha256).hexdigest()


def load_pending_attestations() -> List[Dict[str, Any]]:
    """Load pending attestations from disk, verifying HMAC integrity."""
    if not PENDING_FILE.exists():
        return []
    try:
        raw = PENDING_FILE.read_text()
        envelope = json.loads(raw)
        if not isinstance(envelope, dict) or "attestations" not in envelope:
            # Legacy format (plain list) — discard, cannot verify integrity.
            logger.warning("[agentscore] Discarding unsigned pending attestations")
            return []
        stored_hmac = envelope.get("hmac", "")
        attestations = envelope["attestations"]
        # Verify HMAC
        key = _get_hmac_key()
        payload = json.dumps(attestations, sort_keys=True)
        expected_hmac = _compute_hmac(payload, key)
        if not _hmac_equal(stored_hmac, expected_hmac):
            logger.warning("[agentscore] Pending attestation HMAC mismatch — file may be tampered, discarding")
            return []
        return attestations if isinstance(attestations, list) else []
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("[agentscore] Failed to load pending attestations: %s", e)
        return []


def _hmac_equal(a: str, b: str) -> bool:
    """Constant-time string comparison to prevent timing attacks."""
    import hmac as _hmac

    return _hmac.compare_digest(a, b)


def save_pending_attestations(attestations: List[Dict[str, Any]]) -> None:
    """Save pending attestations to disk with HMAC integrity signature."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    key = _get_hmac_key()
    payload = json.dumps(attestations, sort_keys=True)
    sig = _compute_hmac(payload, key)
    envelope = {
        "attestations": attestations,
        "hmac": sig,
    }
    PENDING_FILE.write_text(json.dumps(envelope, indent=2))


def retry_pending_attestations(chain_client) -> int:
    """
    Retry submitting pending attestations.

    Returns the number of successfully submitted attestations.
    """
    if chain_client is None or not chain_client.is_ready():
        return 0

    pending = load_pending_attestations()
    if not pending:
        return 0

    successful = 0
    remaining = []
    for att in pending:
        try:
            def _hex_to_bytes(h):
                return bytes.fromhex(h[2:]) if h.startswith("0x") else bytes.fromhex(h)

            chain_client.attest(
                tool_calls=att["tool_calls"],
                tool_errors=att["tool_errors"],
                duration=att["duration"],
                completed=att["completed"],
                model_hash=_hex_to_bytes(att.get("model_hash", "0x" + "00" * 32)),
                input_tokens=att.get("input_tokens", 0),
                output_tokens=att.get("output_tokens", 0),
                metrics_hash=_hex_to_bytes(att["metrics_hash"]),
            )
            successful += 1
            logger.info(
                "[agentscore] Retried attestation: tools=%d errors=%d",
                att["tool_calls"],
                att["tool_errors"],
            )
        except Exception as e:
            logger.warning("[agentscore] Retry failed: %s", e)
            remaining.append(att)

    save_pending_attestations(remaining)
    return successful
