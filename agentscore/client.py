"""
AgentScore SDK client (vendored).

Agents use this to:
  1. Register with the relay (gas-free)
  2. Submit attestations via the relay (gas-free)
  3. Query their own score and stats

No web3 dependency — just HTTP calls to the AgentScore server.
"""

import hashlib
import json
import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)


class AgentScoreClient:
    """Lightweight HTTP client for the AgentScore relay API."""

    def __init__(
        self,
        server_url: str = "http://localhost:8000",
        agent_address: str | None = None,
        api_key: str | None = None,
        timeout: int = 30,
    ):
        self.server_url = server_url.rstrip("/")
        self.agent_address = agent_address
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})
        if api_key:
            self._session.headers.update({"X-API-Key": api_key})

    # ── Registration ──────────────────────────────

    def register(
        self,
        name: str | None = None,
        avatar_uri: str | None = None,
    ) -> dict[str, Any]:
        """Register the agent via the relay (gas-free)."""
        if not self.agent_address:
            raise ValueError("agent_address not set")
        body: dict[str, Any] = {"agent": self.agent_address}
        if name:
            body["name"] = name
        if avatar_uri:
            body["avatar_uri"] = avatar_uri
        return self._post("/relay/register", body)

    def set_profile(self, name: str, avatar_uri: str) -> dict[str, Any]:
        """Update the agent's on-chain profile."""
        if not self.agent_address:
            raise ValueError("agent_address not set")
        return self._post("/relay/profile", {
            "agent": self.agent_address,
            "name": name,
            "avatar_uri": avatar_uri,
        })

    # ── Attestation ───────────────────────────────

    def attest(
        self,
        tool_calls: int,
        tool_errors: int,
        duration: int,
        completed: bool,
        model: str,
        input_tokens: int,
        output_tokens: int,
        private_metrics: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Submit a single attestation via the relay."""
        metrics_hash = "0x" + "00" * 32
        if private_metrics:
            metrics_json = json.dumps(private_metrics, sort_keys=True)
            metrics_hash = "0x" + hashlib.sha256(metrics_json.encode()).hexdigest()

        model_hash = "0x" + hashlib.sha256(model.encode()).hexdigest() if model else "0x" + "00" * 32

        attestation = {
            "toolCalls": tool_calls,
            "toolErrors": tool_errors,
            "duration": duration,
            "completed": completed,
            "modelHash": model_hash,
            "inputTokens": input_tokens,
            "outputTokens": output_tokens,
            "metricsHash": metrics_hash,
        }
        return self.attest_batch([attestation])

    def attest_batch(self, attestations: list[dict[str, Any]]) -> dict[str, Any]:
        """Submit multiple attestations via the relay."""
        if not self.agent_address:
            raise ValueError("agent_address not set")
        return self._post("/relay/attest", {
            "agent": self.agent_address,
            "attestations": attestations,
        })

    # ── Queries ───────────────────────────────────

    def get_score(self) -> dict[str, Any]:
        """Get this agent's current score."""
        if not self.agent_address:
            raise ValueError("agent_address not set")
        return self._get(f"/agents/{self.agent_address}/score")

    def get_profile(self) -> dict[str, Any]:
        """Get this agent's profile and stats."""
        if not self.agent_address:
            raise ValueError("agent_address not set")
        return self._get(f"/agents/{self.agent_address}")

    def get_models(self) -> list[dict[str, Any]]:
        """Get model usage for this agent."""
        if not self.agent_address:
            raise ValueError("agent_address not set")
        return self._get(f"/agents/{self.agent_address}/models")

    def get_leaderboard(self, limit: int = 20) -> dict[str, Any]:
        """Get the agent leaderboard."""
        return self._get(f"/agents?limit={limit}")

    def get_stats(self) -> dict[str, Any]:
        """Get global stats."""
        return self._get("/stats")

    # ── Internal ──────────────────────────────────

    def _get(self, path: str) -> Any:
        url = f"{self.server_url}{path}"
        try:
            resp = self._session.get(url, timeout=self.timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            logger.warning("[agentscore-sdk] GET %s failed: %s", path, e)
            raise

    def _post(self, path: str, body: dict[str, Any]) -> Any:
        url = f"{self.server_url}{path}"
        try:
            resp = self._session.post(url, json=body, timeout=self.timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            logger.warning("[agentscore-sdk] POST %s failed: %s", path, e)
            raise
