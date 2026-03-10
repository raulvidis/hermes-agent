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

diff --git a/agentscore/tracker.py b/agentscore/tracker.py
index 05c29c3..8106bd8 100644
--- a/agentscore/tracker.py
+++ b/agentscore/tracker.py
@@ -41,6 +41,10 @@ class InteractionTracker:
             "skipped": 0,
             "errors": 0,
         }
+        self._pending_attestations: List[Dict[str, Any]] = []
+        self._last_flush_time = time.time()
+        self._batch_size_threshold = 25
+        self._batch_time_threshold = 30 * 60  # 30 minutes in seconds
 
     @property
     def stats(self) -> Dict[str, int]:
@@ -58,6 +62,7 @@ class InteractionTracker:
                 "iterations": 0,
                 "tool_names": [],
                 "total_tool_calls": 0,
+                "total_tool_errors": 0,
             }
 
     def on_step(self, context: Dict[str, Any]) -> None:
@@ -75,19 +80,21 @@ class InteractionTracker:
                     "iterations": 0,
                     "tool_names": [],
                     "total_tool_calls": 0,
+                    "total_tool_errors": 0,
                 }
                 self._interactions[session_id] = interaction
 
             iteration = context.get("iteration", 0)
             tool_names = context.get("tool_names", [])
+            tool_errors = context.get("tool_errors", 0)
 
-            interaction["iterations"] = max(
-                interaction["iterations"], iteration
-            )
+            interaction["iterations"] = max(interaction["iterations"], iteration)
             if tool_names:
                 interaction["tool_names"].extend(tool_names)
                 interaction["total_tool_calls"] += len(tool_names)
 
+            interaction["total_tool_errors"] += tool_errors
+
     def on_end(self, context: Dict[str, Any]) -> None:
         """
         Finalize an interaction and attest on-chain.
@@ -115,7 +122,7 @@ class InteractionTracker:
 
         duration = int(time.time() - interaction["started_at"])
         tool_calls = interaction["total_tool_calls"]
-        tool_errors = 0  # Not available from step events; tracked as 0.
+        tool_errors = interaction.get("total_tool_errors", 0)
         completed = True  # agent:end fired = interaction completed.
 
         # Build the private metrics payload (never goes on-chain).
@@ -147,38 +154,170 @@ class InteractionTracker:
         completed: bool,
         private_metrics: Dict[str, Any],
     ) -> None:
-        """Hash private metrics and submit attestation to chain."""
+        """Hash private metrics and queue for batch submission."""
         if self._chain is None or not self._chain.is_ready():
-            # Log what would have been attested (offline mode).
             logger.info(
                 "[agentscore] (offline) tools=%d errors=%d duration=%ds completed=%s",
-                tool_calls, tool_errors, duration, completed,
+                tool_calls,
+                tool_errors,
+                duration,
+                completed,
             )
             return
 
+        metrics_hash = None
         try:
             from web3 import Web3
 
             metrics_json = json.dumps(private_metrics, sort_keys=True)
             metrics_hash = Web3.keccak(text=metrics_json)
 
-            tx_hash = self._chain.attest(
+            with self._lock:
+                self._pending_attestations.append(
+                    {
+                        "tool_calls": tool_calls,
+                        "tool_errors": tool_errors,
+                        "duration": duration,
+                        "completed": completed,
+                        "metrics_hash": metrics_hash,
+                    }
+                )
+                should_flush = (
+                    len(self._pending_attestations) >= self._batch_size_threshold
+                    or time.time() - self._last_flush_time >= self._batch_time_threshold
+                )
+
+            if should_flush:
+                self._flush_batch()
+
+        except Exception as e:
+            self._stats["errors"] += 1
+            logger.warning("[agentscore] Attestation prep failed: %s", e)
+            if metrics_hash is not None:
+                self._save_pending_attestation(
+                    tool_calls=tool_calls,
+                    tool_errors=tool_errors,
+                    duration=duration,
+                    completed=completed,
+                    metrics_hash=metrics_hash.hex(),
+                )
+            return
+
+        try:
+            from web3 import Web3
+
+            metrics_json = json.dumps(private_metrics, sort_keys=True)
+            metrics_hash = Web3.keccak(text=metrics_json)
+
+            with self._lock:
+                self._pending_attestations.append(
+                    {
+                        "tool_calls": tool_calls,
+                        "tool_errors": tool_errors,
+                        "duration": duration,
+                        "completed": completed,
+                        "metrics_hash": metrics_hash,
+                    }
+                )
+                should_flush = (
+                    len(self._pending_attestations) >= self._batch_size_threshold
+                    or time.time() - self._last_flush_time >= self._batch_time_threshold
+                )
+
+            if should_flush:
+                self._flush_batch()
+
+        except Exception as e:
+            self._stats["errors"] += 1
+            logger.warning("[agentscore] Attestation prep failed: %s", e)
+            self._save_pending_attestation(
                 tool_calls=tool_calls,
                 tool_errors=tool_errors,
                 duration=duration,
                 completed=completed,
-                metrics_hash=metrics_hash,
+                metrics_hash=metrics_hash.hex() if metrics_hash else "",
             )
 
-            self._stats["attested"] += 1
+    def _flush_batch(self) -> None:
+        """Flush pending attestations to chain as a batch."""
+        with self._lock:
+            if not self._pending_attestations:
+                return
+            attestations = self._pending_attestations
+            self._pending_attestations = []
+            self._last_flush_time = time.time()
+
+        if not attestations:
+            return
+
+        try:
+            tx_hash = self._chain.attest_batch(attestations)
+            self._stats["attested"] += len(attestations)
             logger.info(
-                "[agentscore] Attested: tools=%d errors=%d duration=%ds tx=%s",
-                tool_calls, tool_errors, duration, tx_hash[:16],
+                "[agentscore] Batch attested: %d attestations tx=%s",
+                len(attestations),
+                tx_hash[:16],
             )
-
         except Exception as e:
             self._stats["errors"] += 1
-            logger.warning("[agentscore] Attestation failed: %s", e)
+            logger.warning("[agentscore] Batch attestation failed: %s", e)
+            for att in attestations:
+                self._save_pending_attestation(
+                    tool_calls=att["tool_calls"],
+                    tool_errors=att["tool_errors"],
+                    duration=att["duration"],
+                    completed=att["completed"],
+                    metrics_hash=att["metrics_hash"].hex()
+                    if hasattr(att["metrics_hash"], "hex")
+                    else att["metrics_hash"],
+                )
+
+    def flush(self) -> None:
+        """Public method to flush pending attestations. Called on shutdown."""
+        self._flush_batch()
+
+        with self._lock:
+            if self._pending_attestations:
+                logger.info(
+                    "[agentscore] Persisting %d unflushed attestations on shutdown",
+                    len(self._pending_attestations),
+                )
+                for att in self._pending_attestations:
+                    self._save_pending_attestation(
+                        tool_calls=att["tool_calls"],
+                        tool_errors=att["tool_errors"],
+                        duration=att["duration"],
+                        completed=att["completed"],
+                        metrics_hash=att["metrics_hash"].hex()
+                        if hasattr(att["metrics_hash"], "hex")
+                        else att["metrics_hash"],
+                    )
+                self._pending_attestations = []
+
+    def _save_pending_attestation(
+        self,
+        tool_calls: int,
+        tool_errors: int,
+        duration: int,
+        completed: bool,
+        metrics_hash: str,
+    ) -> None:
+        """Save a failed attestation to the pending queue for retry."""
+        from .chain import load_pending_attestations, save_pending_attestations
+
+        pending = load_pending_attestations()
+        pending.append(
+            {
+                "tool_calls": tool_calls,
+                "tool_errors": tool_errors,
+                "duration": duration,
+                "completed": completed,
+                "metrics_hash": metrics_hash,
+                "timestamp": int(time.time()),
+            }
+        )
+        save_pending_attestations(pending)
+        logger.info("[agentscore] Saved failed attestation to pending queue")
 
 
 def create_tracker(network: str = "base-sepolia") -> Optional[InteractionTracker]:
@@ -190,13 +329,22 @@ def create_tracker(network: str = "base-sepolia") -> Optional[InteractionTracker
     """
     chain = None
     try:
-        from .chain import AgentScoreChain
+        from .chain import AgentScoreChain, retry_pending_attestations
+
         chain = AgentScoreChain(network=network)
         if chain.is_ready():
             logger.info(
                 "[agentscore] Connected to %s. Agent: %s",
-                network, chain.address,
+                network,
+                chain.address,
             )
+            # Retry any pending attestations from previous failed sessions.
+            try:
+                retried = retry_pending_attestations(chain)
+                if retried > 0:
+                    logger.info("[agentscore] Retried %d pending attestations", retried)
+            except Exception as e:
+                logger.warning("[agentscore] Pending retry failed: %s", e)
             # Auto-register if needed.
             if not chain.is_registered():
                 try:
@@ -218,3 +366,18 @@ def create_tracker(network: str = "base-sepolia") -> Optional[InteractionTracker
         logger.warning("[agentscore] Init error: %s", e)
 
     return InteractionTracker(chain_client=chain)
+
+
+def setup_shutdown_handler(tracker: InteractionTracker) -> None:
+    """Register SIGTERM handler to flush pending attestations on shutdown."""
+    import signal
+
+    def flush_handler(signum, frame):
+        logger.info("[agentscore] SIGTERM received, flushing pending attestations...")
+        if tracker:
+            tracker.flush()
+
+    try:
+        signal.signal(signal.SIGTERM, flush_handler)
+    except (ValueError, OSError):
+        pass  # Not supported on this platform
