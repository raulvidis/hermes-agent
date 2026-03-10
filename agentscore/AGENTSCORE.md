# AgentScore — On-Chain Reputation for AI Agents

> "Equifax for AI agents, built on Base."

## What is AgentScore?

AgentScore is a decentralized protocol that tracks how reliably an AI agent performs over time and turns that into a portable, verifiable score that lives on-chain. Platforms use it to gate access. Humans use it to decide which agents to trust. Agents earn it by doing real work consistently.

**Three properties that make it real:**

- **Portable** — score follows the agent everywhere, across every platform
- **Unfakeable** — backed by on-chain telemetry captured outside the agent's control
- **Gating** — platforms require a minimum score to list or operate, creating real economic stakes

## Scope

This module integrates AgentScore directly into the Hermes gateway. It is:

- **A passive observer** — hooks into the gateway event loop, not the agent's LLM conversation
- **Zero token impact** — never injects into context, never adds messages, never touches the LLM call
- **Privacy-first** — no conversation content, user IDs, session IDs, or platform names go on-chain
- **Opt-in** — disabled by default, enabled with one config flag
- **Gracefully degrading** — if web3 isn't installed or chain is unreachable, everything else works fine

### What it is NOT

- Not a skill (skills inject into context and cost tokens)
- Not a hook in `~/.hermes/hooks/` (external hooks can be tampered with)
- Not a local database (local data can be altered)
- Not a proxy or middleware (no infrastructure to run)

## Architecture

### Why built into the gateway?

The gateway is the **trusted observer**. It sits between the user and the agent. It emits lifecycle events (`agent:start`, `agent:step`, `agent:end`) that the agent process cannot intercept or modify. By wiring AgentScore directly into these event emission points, we capture telemetry that the agent never touches.

```
User sends message
    ↓
Gateway receives message
    ↓
Gateway emits agent:start ──→ Tracker begins in-memory tracking
    ↓
Agent processes (LLM calls, tool usage)
    ↓
Gateway emits agent:step  ──→ Tracker counts: iteration++, tool_names[]
Gateway emits agent:step  ──→ Tracker counts: iteration++, tool_names[]
    ↓
Agent responds
    ↓
Gateway emits agent:end   ──→ Tracker finalizes metrics
                               ├─ Hash private payload (keccak256)
                               ├─ Submit attestation to Base contract
                               └─ Discard in-memory state
```

### Why in-memory only?

Any persistent local storage (SQLite, files, logs) can be read and altered by anyone with access to the machine. The agent could rewrite its own history before it gets attested.

In-memory tracking eliminates this:
- State is built from gateway events as they fire in real-time
- State exists only for the duration of one interaction
- On `agent:end`, it's hashed, attested on-chain, and discarded
- Nothing to tamper with — by the time you could, it's already on-chain

### Why not a local DB?

A local SQLite database can be faked. Anyone with file access can:
- Rewrite session history
- Inflate success rates
- Delete error records
- Fabricate sessions that never happened

This fundamentally breaks trust. AgentScore's value proposition is "unfakeable" — so the data must either never be stored locally, or be attested on-chain before it can be tampered with.

### Why not an external API?

Streaming telemetry to an external API (`agentscore.xyz/ingest`) was considered but rejected for this implementation because:
- **Privacy**: user interaction data would leave the machine
- **Dependency**: requires internet connectivity and a running service
- **Trust**: shifts trust from "on-chain" to "trust our API"
- **Autonomy**: the system should work independently

The on-chain model is better: data stays local until it's reduced to anonymous behavioral numbers, then those numbers go on-chain. No middleman.

## What goes on-chain

Only these fields are written to the AgentScore smart contract:

| Field | Type | Description |
|-------|------|-------------|
| `agent` | address | Pseudonymous keypair (no link to personal identity) |
| `toolCalls` | uint16 | Number of tool calls in the interaction |
| `toolErrors` | uint16 | Number of errored tool calls |
| `duration` | uint32 | Wall-clock seconds |
| `completed` | bool | Whether the interaction finished normally |
| `modelHash` | bytes32 | keccak256 of model identifier (e.g. "anthropic/claude-opus-4-20250514") |
| `inputTokens` | uint32 | Total input/prompt tokens consumed |
| `outputTokens` | uint32 | Total output/completion tokens consumed |
| `metricsHash` | bytes32 | keccak256 of private metrics JSON |
| `timestamp` | uint64 | Block timestamp |

The `modelHash` allows public verification of which model was used: anyone who
knows the model string can hash it and compare against the on-chain value. This
enables per-model reputation tracking (e.g. "how reliable is this agent when
using Claude vs GPT-4?") without leaking any other information.

### What NEVER goes on-chain

- Conversation content (messages, responses)
- User IDs or usernames
- Session IDs
- Platform names (Telegram, Discord, etc.)
- IP addresses
- Tool names or arguments
- Any personally identifiable information

The `metricsHash` allows future verification: if an agent wants to prove its metrics are genuine, it can reveal the private payload and anyone can verify `keccak256(payload) == on-chain hash`. But the payload is never required to be disclosed.

## Technology

### Stack

| Component | Technology | Why |
|-----------|-----------|-----|
| Chain | Base (L2 on Ethereum) | Sub-cent transactions, Coinbase ecosystem, EVM compatible |
| Token | ETH (for gas) | Native to Base, minimal amounts needed |
| Contract | Solidity ^0.8.24 | Standard, auditable, deployed on Base Sepolia (testnet) / Base (mainnet) |
| Client | web3.py >= 7.0 | Python native, matches Hermes stack |
| Identity | secp256k1 keypair | Standard Ethereum keys, generated locally |
| Payment (future) | x402 + USDC | HTTP-native micropayments for score queries |

### Why Base?

- **Cost**: ~$0.001 per attestation (sub-cent L2 fees)
- **Speed**: ~2 second finality
- **Ecosystem**: Coinbase is pushing agent wallets, on-chain AI, x402 protocol
- **Developer pool**: Solidity/EVM is the largest smart contract ecosystem
- **Agent-native**: Base is where the AI agent economy is forming

### Why not Solana/Arbitrum/Polygon?

- **Solana**: Rust/Anchor development is harder, smaller contract ecosystem
- **Arbitrum/Optimism**: Technically fine but less momentum in the agent space
- **Polygon**: Fragmented ecosystem
- **Ethereum L1**: $2-10 per attestation — kills high-frequency writes

## Scoring

The score ranges from 0 to 850 (modeled after credit scores for intuitive understanding).

### Components

| Component | Weight | What it measures |
|-----------|--------|-----------------|
| Completion rate | 35% | Did the agent finish its tasks? |
| Tool success rate | 35% | How cleanly did it use tools? |
| Volume factor | 30% | Confidence from number of attestations |

### Score tiers

| Score | Tier | Meaning |
|-------|------|---------|
| 750-850 | Excellent | Highly reliable, consistent track record |
| 600-749 | Good | Reliable with occasional issues |
| 450-599 | Fair | Works but with notable error rates |
| 300-449 | Developing | Limited history or inconsistent |
| 0-299 | New | Not enough data (minimum 3 attestations) |

### On-chain computation

The score is computed by a `view` function on the smart contract — no additional transaction needed to read it. Anyone can query any agent's score for free (gas-only, no state change). The scoring algorithm is transparent and verifiable since it lives in the contract source code.

## File structure

```
agentscore/
├── __init__.py      # Module docstring, version
├── tracker.py       # In-memory interaction tracking + attestation submission
├── chain.py         # Base contract client (web3.py)
└── identity.py      # secp256k1 keypair generation and storage
```

### Modified files

- `gateway/run.py` — Wires tracker into `agent:start`, `agent:step`, `agent:end` event emission points
- `hermes_cli/config.py` — Adds `agentscore` section to `DEFAULT_CONFIG`
- `pyproject.toml` — Adds `web3>=7.0` as optional dependency under `[agentscore]`

## Configuration

```yaml
# ~/.hermes/config.yaml
agentscore:
  enabled: true
  network: base-sepolia   # "base-sepolia" (testnet) or "base" (mainnet)
```

### Environment variables

| Variable | Purpose |
|----------|---------|
| `AGENTSCORE_CONTRACT` | Contract address (overrides config file) |

### Agent identity

On first run, a keypair is generated at `~/.agentscore/agent.key`. The derived address is the agent's on-chain identity. This key:

- Is generated locally, never transmitted
- Has no link to the user's personal identity
- Can be funded with testnet ETH for Base Sepolia or real ETH for Base mainnet
- Signs all attestation transactions

## Setup

```bash
# 1. Install web3 dependency
pip install web3

# 2. Enable in config
# Add to ~/.hermes/config.yaml:
#   agentscore:
#     enabled: true
#     network: base-sepolia

# 3. Set contract address
export AGENTSCORE_CONTRACT=0x...

# 4. Fund agent wallet (printed on first gateway start)
# Get testnet ETH from: https://www.coinbase.com/faucets/base-ethereum-goerli-faucet

# 5. Restart gateway
hermes gateway run --replace
```

## Business model (future)

- **Earning reputation**: Free — the protocol subsidizes attestation gas or agents pay sub-cent fees
- **Querying reputation**: Paid via x402 micropayments (USDC on Base)
- **Platform gating**: Platforms pay to query scores before granting agent access
- **Network effect**: More agents and platforms = more valuable scores

## Future work

- **x402 integration**: Pay-per-query score API using HTTP 402 + USDC
- **Tool error detection**: Enrich `agent:step` context with tool outcome data
- **Cross-platform portability**: Score follows the agent across any framework
- **Verification agents**: Separate agents that spot-check output quality
- **Proxy gateway**: Agent-agnostic scoring via API URL swap (works with any framework)
- **Batch attestation**: Merkle tree of multiple interactions in a single transaction
