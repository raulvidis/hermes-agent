commit 109bfb6b4deddba2969966ecc426d494008512e1
Author: opencode <opencode@rvlabs.tech>
Date:   Tue Mar 10 19:36:38 2026 +0000

    fix(agentscore): batch attestation, error tracking, retry logic, tests
    
    - Track tool_errors from agent:step events instead of hardcoding to 0
    - Batch attestation: accumulate pending, flush at count >= 25 or time >= 30m
    - Add attestBatch to contract ABI and AgentScoreChain
    - Persist failed/unflushed attestations to ~/.agentscore/pending.json
    - Retry pending attestations on startup
    - SIGTERM handler flushes pending before exit
    - Log warning when key file chmod fails
    - Add tests/test_agentscore.py (17 tests covering lifecycle, errors,
      batching, persistence, offline mode)

diff --git a/agentscore/chain.py b/agentscore/chain.py
index b452ae3..96c582e 100644
--- a/agentscore/chain.py
+++ b/agentscore/chain.py
@@ -12,7 +12,7 @@ import json
 import logging
 import os
 from pathlib import Path
-from typing import Any, Dict, Optional
+from typing import Any, Dict, List, Optional
 
 logger = logging.getLogger(__name__)
 
@@ -51,6 +51,25 @@ CONTRACT_ABI = [
         "stateMutability": "nonpayable",
         "type": "function",
     },
+    {
+        "inputs": [
+            {
+                "components": [
+                    {"name": "toolCalls", "type": "uint16"},
+                    {"name": "toolErrors", "type": "uint16"},
+                    {"name": "duration", "type": "uint32"},
+                    {"name": "completed", "type": "bool"},
+                    {"name": "metricsHash", "type": "bytes32"},
+                ],
+                "name": "attestations",
+                "type": "tuple[]",
+            }
+        ],
+        "name": "attestBatch",
+        "outputs": [],
+        "stateMutability": "nonpayable",
+        "type": "function",
+    },
     {
         "inputs": [
             {"name": "agent", "type": "address"},
@@ -82,6 +101,7 @@ CONTRACT_ABI = [
 
 CONFIG_DIR = Path.home() / ".agentscore"
 DEPLOY_FILE = CONFIG_DIR / "contract.json"
+PENDING_FILE = CONFIG_DIR / "pending.json"
 
 
 class AgentScoreChain:
@@ -139,9 +159,7 @@ class AgentScoreChain:
 
     def register(self) -> str:
         """Register the agent on-chain.  Returns tx hash."""
-        tx = self.contract.functions.register().build_transaction(
-            self._tx_params()
-        )
+        tx = self.contract.functions.register().build_transaction(self._tx_params())
         return self._sign_and_send(tx)
 
     def attest(
@@ -172,14 +190,43 @@ class AgentScoreChain:
         ).build_transaction(self._tx_params())
         return self._sign_and_send(tx)
 
+    def attest_batch(self, attestations: List[Dict[str, Any]]) -> str:
+        """
+        Submit multiple behavioral attestations in a single transaction.
+
+        Args:
+            attestations: List of attestation dicts with keys:
+                - tool_calls: int
+                - tool_errors: int
+                - duration: int
+                - completed: bool
+                - metrics_hash: bytes
+
+        Returns tx hash.
+        """
+        formatted = []
+        for att in attestations:
+            formatted.append(
+                (
+                    min(att["tool_calls"], 65535),
+                    min(att["tool_errors"], 65535),
+                    min(att["duration"], 2**32 - 1),
+                    att["completed"],
+                    att["metrics_hash"],
+                )
+            )
+
+        tx = self.contract.functions.attestBatch(formatted).build_transaction(
+            self._tx_params()
+        )
+        return self._sign_and_send(tx)
+
     def get_score(self, agent: str = None, window: int = 0) -> Dict[str, int]:
         """Query an agent's on-chain score."""
         from web3 import Web3
 
         agent = Web3.to_checksum_address(agent or self.address)
-        score, sessions = self.contract.functions.getScore(
-            agent, window
-        ).call()
+        score, sessions = self.contract.functions.getScore(agent, window).call()
         return {"score": score, "sessions": sessions}
 
     def get_attestation_count(self, agent: str = None) -> int:
@@ -198,9 +245,64 @@ class AgentScoreChain:
         }
 
     def _sign_and_send(self, tx: dict) -> str:
-        signed = self.w3.eth.account.sign_transaction(
-            tx, private_key=self.private_key
-        )
+        signed = self.w3.eth.account.sign_transaction(tx, private_key=self.private_key)
         tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
         receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)
         return receipt.transactionHash.hex()
+
+
+def load_pending_attestations() -> List[Dict[str, Any]]:
+    """Load pending attestations from disk."""
+    if not PENDING_FILE.exists():
+        return []
+    try:
+        data = json.loads(PENDING_FILE.read_text())
+        return data if isinstance(data, list) else []
+    except (json.JSONDecodeError, OSError):
+        return []
+
+
+def save_pending_attestations(attestations: List[Dict[str, Any]]) -> None:
+    """Save pending attestations to disk."""
+    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
+    PENDING_FILE.write_text(json.dumps(attestations, indent=2))
+
+
+def retry_pending_attestations(chain_client) -> int:
+    """
+    Retry submitting pending attestations.
+
+    Returns the number of successfully submitted attestations.
+    """
+    if chain_client is None or not chain_client.is_ready():
+        return 0
+
+    pending = load_pending_attestations()
+    if not pending:
+        return 0
+
+    successful = 0
+    remaining = []
+    for att in pending:
+        try:
+            chain_client.attest(
+                tool_calls=att["tool_calls"],
+                tool_errors=att["tool_errors"],
+                duration=att["duration"],
+                completed=att["completed"],
+                metrics_hash=bytes.fromhex(att["metrics_hash"][2:])
+                if att["metrics_hash"].startswith("0x")
+                else bytes.fromhex(att["metrics_hash"]),
+            )
+            successful += 1
+            logger.info(
+                "[agentscore] Retried attestation: tools=%d errors=%d",
+                att["tool_calls"],
+                att["tool_errors"],
+            )
+        except Exception as e:
+            logger.warning("[agentscore] Retry failed: %s", e)
+            remaining.append(att)
+
+    save_pending_attestations(remaining)
+    return successful
