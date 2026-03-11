"""
In-memory interaction tracker for AgentScore.

Tracks each agent interaction (user message -> agent response cycle)
purely in memory.  On interaction end, submits an attestation to the
AgentScore relay server via HTTP (no web3/gas needed).

Design principles:
  - Zero token impact: runs outside the LLM conversation loop.
  - Zero persistence: all state is in-memory, discarded after attestation.
  - Privacy-first: only tool_calls, tool_errors, duration, completed go
    on-chain.  No user IDs, session IDs, messages, or platform names.
  - No blockchain dependency: agents talk to the relay server over HTTP.
"""

import hashlib
import json
import logging
import time
from threading import Lock
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class InteractionTracker:
    """
    Tracks in-flight interactions and attests completed ones via the relay.

    Thread-safe — the gateway fires events from both async and sync
    contexts via run_coroutine_threadsafe.
    """

    def __init__(self, sdk_client=None):
        self._interactions: Dict[str, Dict[str, Any]] = {}
        self._lock = Lock()
        self._sdk = sdk_client
        self._stats = {
            "tracked": 0,
            "attested": 0,
            "skipped": 0,
            "errors": 0,
        }
        self._pending_attestations: List[Dict[str, Any]] = []
        self._last_flush_time = time.time()
        self._batch_size_threshold = 1   # Flush after every interaction (testnet)
        self._batch_time_threshold = 30 * 60  # 30 minutes in seconds

    @property
    def stats(self) -> Dict[str, int]:
        return dict(self._stats)

    def on_start(self, context: Dict[str, Any]) -> None:
        """Begin tracking an interaction.  Called on agent:start."""
        session_id = context.get("session_id", "")
        if not session_id:
            return

        with self._lock:
            self._interactions[session_id] = {
                "started_at": time.time(),
                "iterations": 0,
                "tool_names": [],
                "total_tool_calls": 0,
                "total_tool_errors": 0,
                "model": context.get("model", ""),
                "input_tokens": 0,
                "output_tokens": 0,
            }

    def on_step(self, context: Dict[str, Any]) -> None:
        """Record a tool-calling iteration.  Called on agent:step."""
        session_id = context.get("session_id", "")
        if not session_id:
            return

        with self._lock:
            interaction = self._interactions.get(session_id)
            if interaction is None:
                interaction = {
                    "started_at": time.time(),
                    "iterations": 0,
                    "tool_names": [],
                    "total_tool_calls": 0,
                    "total_tool_errors": 0,
                    "model": context.get("model", ""),
                    "input_tokens": 0,
                    "output_tokens": 0,
                }
                self._interactions[session_id] = interaction

            iteration = context.get("iteration", 0)
            tool_names = context.get("tool_names", [])
            tool_errors = context.get("tool_errors", 0)

            interaction["iterations"] = max(interaction["iterations"], iteration)
            if tool_names:
                interaction["tool_names"].extend(tool_names)
                interaction["total_tool_calls"] += len(tool_names)

            interaction["total_tool_errors"] += tool_errors
            interaction["input_tokens"] += context.get("input_tokens", 0)
            interaction["output_tokens"] += context.get("output_tokens", 0)

            model = context.get("model", "")
            if model:
                interaction["model"] = model

    def on_end(self, context: Dict[str, Any]) -> None:
        """Finalize an interaction and submit attestation."""
        session_id = context.get("session_id", "")
        if not session_id:
            return

        with self._lock:
            interaction = self._interactions.pop(session_id, None)

        if interaction is None:
            return

        self._stats["tracked"] += 1

        if interaction["total_tool_calls"] == 0:
            self._stats["skipped"] += 1
            return

        duration = int(time.time() - interaction["started_at"])
        tool_calls = interaction["total_tool_calls"]
        tool_errors = interaction.get("total_tool_errors", 0)
        completed = context.get("completed", "error" not in context)
        model = interaction.get("model", "")
        input_tokens = interaction.get("input_tokens", 0)
        output_tokens = interaction.get("output_tokens", 0)

        private_metrics = {
            "tool_calls": tool_calls,
            "tool_errors": tool_errors,
            "duration": duration,
            "completed": completed,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "iterations": interaction["iterations"],
            "tool_names": sorted(set(interaction["tool_names"])),
            "timestamp": int(time.time()),
        }

        self._submit_attestation(
            tool_calls=tool_calls,
            tool_errors=tool_errors,
            duration=duration,
            completed=completed,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            private_metrics=private_metrics,
        )

    def _submit_attestation(
        self,
        tool_calls: int,
        tool_errors: int,
        duration: int,
        completed: bool,
        model: str,
        input_tokens: int,
        output_tokens: int,
        private_metrics: Dict[str, Any],
    ) -> None:
        """Hash private metrics and queue for batch submission via relay."""
        if self._sdk is None:
            logger.info(
                "[agentscore] (offline) model=%s tools=%d errors=%d duration=%ds "
                "tokens=%d/%d completed=%s",
                model, tool_calls, tool_errors, duration,
                input_tokens, output_tokens, completed,
            )
            return

        try:
            metrics_json = json.dumps(private_metrics, sort_keys=True)
            metrics_hash = "0x" + hashlib.sha256(metrics_json.encode()).hexdigest()
            model_hash = "0x" + hashlib.sha256(model.encode()).hexdigest() if model else "0x" + "00" * 32

            with self._lock:
                self._pending_attestations.append({
                    "tool_calls": tool_calls,
                    "tool_errors": tool_errors,
                    "duration": duration,
                    "completed": completed,
                    "model_hash": model_hash,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "metrics_hash": metrics_hash,
                })
                should_flush = (
                    len(self._pending_attestations) >= self._batch_size_threshold
                    or time.time() - self._last_flush_time >= self._batch_time_threshold
                )

            if should_flush:
                self._flush_batch()

        except Exception as e:
            self._stats["errors"] += 1
            logger.warning("[agentscore] Attestation prep failed: %s", e)

    def reap_stale(self, max_age: int = 3600) -> int:
        """Reap interactions open longer than max_age seconds."""
        now = time.time()
        stale_ids: List[str] = []

        with self._lock:
            for sid, interaction in self._interactions.items():
                if now - interaction["started_at"] > max_age:
                    stale_ids.append(sid)

        reaped = 0
        for sid in stale_ids:
            self.on_end({
                "session_id": sid,
                "completed": False,
                "error": "stale interaction reaped",
            })
            reaped += 1

        if reaped:
            logger.info("[agentscore] Reaped %d stale interactions", reaped)
        return reaped

    def _flush_batch(self) -> None:
        """Flush pending attestations to the relay server."""
        with self._lock:
            if not self._pending_attestations:
                return
            attestations = self._pending_attestations
            self._pending_attestations = []
            self._last_flush_time = time.time()

        if not attestations:
            return

        try:
            result = self._sdk.attest_batch(attestations)
            self._stats["attested"] += len(attestations)
            tx_hash = result.get("txHash", "unknown")
            logger.info(
                "[agentscore] Batch attested: %d attestations tx=%s",
                len(attestations),
                tx_hash[:16] if len(tx_hash) > 16 else tx_hash,
            )
        except Exception as e:
            self._stats["errors"] += 1
            logger.warning("[agentscore] Batch attestation failed: %s", e)

    def flush(self) -> None:
        """Public method to flush pending attestations. Called on shutdown."""
        self._flush_batch()


def create_tracker(
    server_url: str = "http://localhost:8000",
) -> Optional[InteractionTracker]:
    """
    Create a tracker connected to the AgentScore relay server.

    The agent's identity (address) comes from ~/.agentscore/agent.key.
    Registration is handled via the relay (gas-free).
    """
    sdk = None
    try:
        import requests  # noqa: F401 — verify requests is available
        from .identity import ensure_identity, get_wallet_provider

        _, agent_address = ensure_identity()
        wallet_type = get_wallet_provider()

        try:
            from agentscore_sdk import AgentScoreClient
        except ImportError:
            from ._sdk_client import AgentScoreClient

        sdk = AgentScoreClient(
            server_url=server_url,
            agent_address=agent_address,
        )

        # Register if not already registered (gas-free via relay)
        try:
            sdk.get_profile()
            logger.info(
                "[agentscore] Connected to %s. Agent: %s (wallet: %s)",
                server_url, agent_address, wallet_type,
            )
        except Exception:
            try:
                sdk.register()
                logger.info(
                    "[agentscore] Registered agent %s via relay (wallet: %s)",
                    agent_address, wallet_type,
                )
            except Exception as e:
                logger.warning("[agentscore] Registration failed: %s", e)

    except ImportError as e:
        logger.info(
            "[agentscore] Missing dependency (%s). "
            "Install with: pip install requests — running in offline mode.", e
        )
    except Exception as e:
        logger.warning("[agentscore] Init error: %s", e)

    return InteractionTracker(sdk_client=sdk)


def setup_shutdown_handler(tracker: InteractionTracker) -> None:
    """Register SIGTERM handler to flush pending attestations on shutdown."""
    import signal

    def flush_handler(signum, frame):
        logger.info("[agentscore] SIGTERM received, flushing pending attestations...")
        if tracker:
            tracker.flush()

    try:
        signal.signal(signal.SIGTERM, flush_handler)
    except (ValueError, OSError):
        pass
