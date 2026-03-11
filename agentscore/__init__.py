"""
AgentScore — on-chain reputation for AI agents.

A built-in module that passively tracks agent behavioral metrics
(tool usage, completion rates, error rates) and attests them via
the AgentScore relay server. Runs entirely in the agent event loop
with zero token impact.

Wallet: Coinbase CDP MPC wallet (preferred) or local keypair fallback.
No web3 dependency needed — attestations go through the relay server.

Enable in ~/.hermes/config.yaml:
    agentscore:
      enabled: true
      server_url: http://your-agentscore-server:8000
"""

__version__ = "0.1.0"
