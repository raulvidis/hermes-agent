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
from typing import Any, Dict, Optional

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
            {"name": "metricsHash", "type": "bytes32"},
        ],
        "name": "attest",
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
        tx = self.contract.functions.register().build_transaction(
            self._tx_params()
        )
        return self._sign_and_send(tx)

    def attest(
        self,
        tool_calls: int,
        tool_errors: int,
        duration: int,
        completed: bool,
        metrics_hash: bytes,
    ) -> str:
        """
        Submit a behavioral attestation.  Returns tx hash.

        Args:
            tool_calls:   Number of tool calls in the interaction.
            tool_errors:  Number of errored tool calls.
            duration:     Wall-clock seconds.
            completed:    Whether the interaction completed.
            metrics_hash: keccak256 of the private metrics payload
                          (never sent on-chain, only the hash).
        """
        tx = self.contract.functions.attest(
            min(tool_calls, 65535),
            min(tool_errors, 65535),
            min(duration, 2**32 - 1),
            completed,
            metrics_hash,
        ).build_transaction(self._tx_params())
        return self._sign_and_send(tx)

    def get_score(self, agent: str = None, window: int = 0) -> Dict[str, int]:
        """Query an agent's on-chain score."""
        from web3 import Web3

        agent = Web3.to_checksum_address(agent or self.address)
        score, sessions = self.contract.functions.getScore(
            agent, window
        ).call()
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
        signed = self.w3.eth.account.sign_transaction(
            tx, private_key=self.private_key
        )
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)
        return receipt.transactionHash.hex()
