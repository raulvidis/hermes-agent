"""AgentScore integration for Hermes Agent.

Provides automatic attestation tracking that captures metrics directly
from the framework's execution loop — not self-reported by the operator.
"""

import logging
import os

from .tracker import AgentScoreTracker

logger = logging.getLogger(__name__)

__all__ = ["AgentScoreTracker", "create_tracker_from_env"]


def create_tracker_from_env() -> AgentScoreTracker | None:
    """Create a tracker from AGENTSCORE_* environment variables.

    Required env vars:
        AGENTSCORE_SERVER_URL   — AgentScore server URL
        AGENTSCORE_AGENT_ADDRESS — Agent's Ethereum address
        AGENTSCORE_API_KEY      — Relay API key

    Returns None if any are missing.
    """
    server_url = os.environ.get("AGENTSCORE_SERVER_URL")
    agent_address = os.environ.get("AGENTSCORE_AGENT_ADDRESS")
    api_key = os.environ.get("AGENTSCORE_API_KEY")

    if not all([server_url, agent_address, api_key]):
        return None

    logger.debug("AgentScore tracker configured: %s", server_url)
    return AgentScoreTracker(server_url, agent_address, api_key)
