"""
AgentScore — on-chain reputation for AI agents.

A built-in module that passively tracks agent behavioral metrics
(tool usage, completion rates, error rates) and attests them to
the AgentScore contract on Base. Runs entirely in the gateway
event loop with zero token impact on the agent.

Privacy guarantees:
  - No conversation content goes on-chain
  - No user IDs, session IDs, or platform names on-chain
  - Only pseudonymous agent address + aggregate behavioral numbers
  - metrics_hash allows verification without revealing raw data

Enable in ~/.hermes/config.yaml:
    agentscore:
      enabled: true
      network: base-sepolia   # or "base" for mainnet
"""

__version__ = "0.1.0"
