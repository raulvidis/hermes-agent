"""
Vendored minimal AgentScore SDK client.

HTTP-only — no web3 dependency. Talks to the AgentScore relay server.
"""

import hashlib
import json
import logging
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)


class AgentScoreClient:
    """Lightweight HTTP client for the AgentScore relay API."""

    def __init__(
        self,
        server_url: str = "http://localhost:8000",
        agent_address: Optional[str] = None,
        timeout: int = 30,
    ):
        self.server_url = server_url.rstrip("/")
        self.agent_address = agent_address
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})

    def register(
        self,
        name: Optional[str] = None,
        avatar_uri: Optional[str] = None,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {"agent": self.agent_address}
        if name:
            body["name"] = name
        if avatar_uri:
            body["avatar_uri"] = avatar_uri
        return self._post("/relay/register", body)

    def attest_batch(self, attestations: List[Dict[str, Any]]) -> Dict[str, Any]:
        return self._post("/relay/attest", {
            "agent": self.agent_address,
            "attestations": attestations,
        })

    def get_score(self) -> Dict[str, Any]:
        return self._get(f"/agents/{self.agent_address}/score")

    def get_profile(self) -> Dict[str, Any]:
        return self._get(f"/agents/{self.agent_address}")

    def _get(self, path: str) -> Any:
        resp = self._session.get(
            f"{self.server_url}{path}", timeout=self.timeout
        )
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, body: Dict[str, Any]) -> Any:
        resp = self._session.post(
            f"{self.server_url}{path}", json=body, timeout=self.timeout
        )
        resp.raise_for_status()
        return resp.json()
