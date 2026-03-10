"""
In-memory interaction tracker for AgentScore.

Tracks each agent interaction (user message → agent response cycle)
purely in memory.  On interaction end, computes a privacy-preserving
metrics hash and submits an on-chain attestation.

Design principles:
  - Zero token impact: runs outside the LLM conversation loop.
  - Zero persistence: all state is in-memory, discarded after attestation.
  - Privacy-first: only tool_calls, tool_errors, duration, completed go
    on-chain.  No user IDs, session IDs, messages, or platform names.
  - Tamper-proof: metrics are captured from gateway events that the agent
    process cannot intercept or modify.
"""

import json
import logging
import time
from threading import Lock
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class InteractionTracker:
    """
    Tracks in-flight interactions and attests completed ones on-chain.

    Thread-safe — the gateway fires events from both async and sync
    contexts via run_coroutine_threadsafe.
    """

    def __init__(self, chain_client=None):
        self._interactions: Dict[str, Dict[str, Any]] = {}
        self._lock = Lock()
        self._chain = chain_client
        self._stats = {
            "tracked": 0,
            "attested": 0,
            "skipped": 0,
            "errors": 0,
        }

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
            }

    def on_step(self, context: Dict[str, Any]) -> None:
        """Record a tool-calling iteration.  Called on agent:step."""
        session_id = context.get("session_id", "")
        if not session_id:
            return

        with self._lock:
            interaction = self._interactions.get(session_id)
            if interaction is None:
                # Missed agent:start — create a late entry.
                interaction = {
                    "started_at": time.time(),
                    "iterations": 0,
                    "tool_names": [],
                    "total_tool_calls": 0,
                }
                self._interactions[session_id] = interaction

            iteration = context.get("iteration", 0)
            tool_names = context.get("tool_names", [])

            interaction["iterations"] = max(
                interaction["iterations"], iteration
            )
            if tool_names:
                interaction["tool_names"].extend(tool_names)
                interaction["total_tool_calls"] += len(tool_names)

    def on_end(self, context: Dict[str, Any]) -> None:
        """
        Finalize an interaction and attest on-chain.

        Called on agent:end.  Pops the in-memory state, computes
        metrics, and submits.  Interactions with no tool calls are
        skipped (simple chat exchanges don't contribute to reputation).
        """
        session_id = context.get("session_id", "")
        if not session_id:
            return

        with self._lock:
            interaction = self._interactions.pop(session_id, None)

        if interaction is None:
            return

        self._stats["tracked"] += 1

        # Skip trivial interactions (no tool usage = nothing to score).
        if interaction["total_tool_calls"] == 0:
            self._stats["skipped"] += 1
            return

        duration = int(time.time() - interaction["started_at"])
        tool_calls = interaction["total_tool_calls"]
        tool_errors = 0  # Not available from step events; tracked as 0.
        completed = True  # agent:end fired = interaction completed.

        # Build the private metrics payload (never goes on-chain).
        # Only its keccak256 hash is attested, allowing future
        # verification without revealing the data.
        private_metrics = {
            "tool_calls": tool_calls,
            "tool_errors": tool_errors,
            "duration": duration,
            "completed": completed,
            "iterations": interaction["iterations"],
            "tool_names": sorted(set(interaction["tool_names"])),
            "timestamp": int(time.time()),
        }

        self._submit_attestation(
            tool_calls=tool_calls,
            tool_errors=tool_errors,
            duration=duration,
            completed=completed,
            private_metrics=private_metrics,
        )

    def _submit_attestation(
        self,
        tool_calls: int,
        tool_errors: int,
        duration: int,
        completed: bool,
        private_metrics: Dict[str, Any],
    ) -> None:
        """Hash private metrics and submit attestation to chain."""
        if self._chain is None or not self._chain.is_ready():
            # Log what would have been attested (offline mode).
            logger.info(
                "[agentscore] (offline) tools=%d errors=%d duration=%ds completed=%s",
                tool_calls, tool_errors, duration, completed,
            )
            return

        try:
            from web3 import Web3

            metrics_json = json.dumps(private_metrics, sort_keys=True)
            metrics_hash = Web3.keccak(text=metrics_json)

            tx_hash = self._chain.attest(
                tool_calls=tool_calls,
                tool_errors=tool_errors,
                duration=duration,
                completed=completed,
                metrics_hash=metrics_hash,
            )

            self._stats["attested"] += 1
            logger.info(
                "[agentscore] Attested: tools=%d errors=%d duration=%ds tx=%s",
                tool_calls, tool_errors, duration, tx_hash[:16],
            )

        except Exception as e:
            self._stats["errors"] += 1
            logger.warning("[agentscore] Attestation failed: %s", e)


def create_tracker(network: str = "base-sepolia") -> Optional[InteractionTracker]:
    """
    Create a tracker with chain client.

    Returns None if web3 is not installed or chain connection fails.
    Gracefully degrades — the gateway runs fine without it.
    """
    chain = None
    try:
        from .chain import AgentScoreChain
        chain = AgentScoreChain(network=network)
        if chain.is_ready():
            logger.info(
                "[agentscore] Connected to %s. Agent: %s",
                network, chain.address,
            )
            # Auto-register if needed.
            if not chain.is_registered():
                try:
                    tx = chain.register()
                    logger.info("[agentscore] Registered on-chain: %s", tx)
                except Exception as e:
                    logger.warning("[agentscore] Registration failed: %s", e)
        else:
            logger.info(
                "[agentscore] Chain not ready (no contract address). "
                "Running in offline mode."
            )
    except ImportError:
        logger.info(
            "[agentscore] web3 not installed. "
            "Install with: pip install web3  — running in offline mode."
        )
    except Exception as e:
        logger.warning("[agentscore] Init error: %s", e)

    return InteractionTracker(chain_client=chain)
