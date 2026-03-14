"""AgentScore session tracker for Hermes Agent.

Captures metrics directly from the framework's execution loop:
  - Token counts from LLM API responses
  - Tool call success/failure from _detect_tool_failure()
  - Session duration from wall-clock time
  - Completion status from framework logic

Submits attestation via a background daemon thread on session end
so it never blocks the agent.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from typing import Any

from .client import AgentScoreClient

logger = logging.getLogger(__name__)


class AgentScoreTracker:
    """Tracks a single agent session and submits attestation on end."""

    def __init__(self, server_url: str, agent_address: str, api_key: str):
        self._client = AgentScoreClient(
            server_url=server_url,
            agent_address=agent_address,
            api_key=api_key,
        )
        self._model: str = ""
        self._start_time: float = 0.0
        self._tool_calls: int = 0
        self._tool_errors: int = 0
        self._input_tokens: int = 0
        self._output_tokens: int = 0

    def start_session(self, model: str) -> None:
        """Reset counters and record start time for a new session."""
        self._model = self._normalize_model(model)
        self._start_time = time.time()
        self._tool_calls = 0
        self._tool_errors = 0
        self._input_tokens = 0
        self._output_tokens = 0
        logger.debug("AgentScore: session started (model=%s)", self._model)

    def record_tool_call(self, is_error: bool = False) -> None:
        """Record a tool call, optionally marking it as an error."""
        self._tool_calls += 1
        if is_error:
            self._tool_errors += 1

    def record_tokens(self, input_tokens: int, output_tokens: int) -> None:
        """Accumulate token counts from an API response."""
        self._input_tokens += input_tokens
        self._output_tokens += output_tokens

    def end_session(self, completed: bool = True) -> None:
        """Submit attestation in a background daemon thread."""
        duration = int(time.time() - self._start_time) if self._start_time else 0

        # Build metrics dict for hash verification
        metrics = {
            "tool_calls": self._tool_calls,
            "tool_errors": self._tool_errors,
            "duration": duration,
            "completed": completed,
            "model": self._model,
            "input_tokens": self._input_tokens,
            "output_tokens": self._output_tokens,
        }

        thread = threading.Thread(
            target=self._submit,
            args=(metrics,),
            daemon=True,
        )
        thread.start()
        logger.debug("AgentScore: session end queued (completed=%s)", completed)

    @staticmethod
    def _normalize_model(model: str) -> str:
        """Normalize model name for consistent hashing.

        Strips provider prefixes (e.g. 'anthropic/claude-opus-4.6' -> 'claude-opus-4-6')
        and replaces dots with hyphens.
        """
        # Strip provider prefix
        if "/" in model:
            model = model.split("/", 1)[1]
        # Replace dots with hyphens
        model = model.replace(".", "-")
        return model

    def _submit(self, metrics: dict[str, Any]) -> None:
        """Submit attestation to the server. Never raises."""
        try:
            metrics_json = json.dumps(metrics, sort_keys=True)
            metrics_hash = "0x" + hashlib.sha256(metrics_json.encode()).hexdigest()

            self._client.attest(
                tool_calls=metrics["tool_calls"],
                tool_errors=metrics["tool_errors"],
                duration=metrics["duration"],
                completed=metrics["completed"],
                model=metrics["model"],
                input_tokens=metrics["input_tokens"],
                output_tokens=metrics["output_tokens"],
                private_metrics=metrics,
            )
            logger.debug("AgentScore: attestation submitted")
        except Exception:
            logger.debug("AgentScore: attestation submission failed", exc_info=True)
