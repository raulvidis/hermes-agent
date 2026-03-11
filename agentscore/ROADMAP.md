# AgentScore — Roadmap & Next Steps

## Current Status

**Branch**: `feat/agentscore-clean`
**Tests**: 29 passing
**State**: Core tracking, attestation pipeline, and gateway integration complete. No smart contract deployed yet.

### What's working
- In-memory interaction tracking (start → step → end lifecycle)
- On-chain attestation pipeline (register, attest, attestBatch via web3.py)
- Agent identity (secp256k1 keypair at `~/.agentscore/agent.key`)
- Batch accumulation (25 count / 30 min time threshold)
- Pending attestation persistence with HMAC integrity signing
- Model hash + input/output token tracking (on-chain)
- Tool error detection from `_detect_tool_failure`
- Completed status from gateway context (interrupted, error, budget exhausted)
- Stale interaction reaping method (not yet scheduled)
- Graceful degradation (offline mode when web3/contract unavailable)
- Shutdown handler for SIGTERM flush

---

## Phase 1: Smart Contract (Blocker)

### 1.1 Write the Solidity contract
- [x] Create `agentscore/contracts/AgentScore.sol`
- [x] Functions: `register()`, `attest()`, `attestBatch()`, `getScore()`, `getAttestationCount()`
- [x] On-chain scoring algorithm (0–850 scale):
  - Completion rate: 35%
  - Tool success rate: 35%
  - Volume factor: 30%
- [x] Storage: per-agent attestation array + registration mapping
- [x] Events for indexing: `Registered(address)`, `Attested(address, uint256 index)`
- [x] Foundry project with 20 passing tests (`forge test`)
- [x] Deployment script (`script/Deploy.s.sol`)

### 1.2 Deploy to Base Sepolia (testnet)
- [x] Set up Foundry project (`agentscore/contracts/`)
- [x] Write deployment script (`script/Deploy.s.sol` + `script/deploy.sh`)
- [x] Deploy to Base Sepolia: `0x0414E5f85dB19072282b598Eb0a8B78e502aB767`
- [x] Save contract address to `~/.agentscore/contract.json`
- [ ] Verify on BaseScan (needs `BASESCAN_API_KEY`)
- [x] Document faucet instructions (Base Sepolia ETH)

### 1.3 End-to-end test
- [x] Fund agent wallet with testnet ETH
- [x] Run CLI chat with `agentscore.enabled: true`
- [x] Verify attestations land on-chain via BaseScan
- [ ] Query score via `getScore()` and validate (need 3+ attestations)

### 1.4 Future: Gas-free attestation (not blocking)
- Option A: Meta-transaction relayer (agent signs, relayer pays gas)
- Option B: Coinbase Paymaster (EIP-4337, requires smart accounts)
- Option C: Merkle root anchoring (O(1) gas regardless of agent count)
- Decision: keep V1 direct attestation for now, revisit at scale

---

## Phase 2: Fix Known Issues

### 2.1 Schedule stale interaction reaper
- [ ] `reap_stale()` exists but is never called
- [ ] Add periodic background task in gateway (every 30 min)
- [ ] Reap sessions open > 1 hour as `completed=False`
- [ ] Test with long-running sessions

### 2.2 Improve error handling in _submit_attestation
- [ ] Only save pending attestation if preparation fully succeeds
- [ ] Don't save partial data when keccak fails

### 2.3 Add missing tests
- [ ] `create_tracker()` factory function (import errors, chain connection failures)
- [ ] Offline initialization paths (no web3, no contract address, unreachable chain)
- [ ] SIGTERM handler integration (signal delivery + flush verification)
- [ ] Negative token delta handling

---

## Phase 3: Scoring & Querying

### 3.1 On-chain scoring view function
- [ ] Implement weighted scoring in Solidity:
  ```
  score = (completionRate * 35 + toolSuccessRate * 35 + volumeFactor * 30) * 850 / 100
  ```
- [ ] Windowed scoring (last N attestations or last T seconds)
- [ ] Minimum attestation threshold (3) before score is valid

### 3.2 Score query from gateway
- [ ] CLI command: `hermes agentscore` — show agent's score, attestation count, address
- [ ] Gateway startup log: print current score if registered
- [ ] API endpoint (optional): expose score via gateway HTTP

### 3.3 Per-model analytics
- [ ] `getModelStats(agent, modelHash)` — score breakdown by model
- [ ] Enables "how reliable is this agent on Claude vs GPT-4?"
- [ ] Token cost tracking per model

---

## Phase 4: Platform Gating

### 4.1 Minimum score requirements
- [ ] Platforms can query an agent's score before granting access
- [ ] Configuration: `min_score: 600` to gate entry
- [ ] Gradual rollout: warn-only mode first, then enforce

### 4.2 Score badges
- [ ] On-chain tier mapping (Excellent/Good/Fair/Developing/New)
- [ ] Display in gateway status / bot profile

---

## Phase 5: x402 Micropayments

### 5.1 Pay-per-query score API
- [ ] HTTP 402 protocol for score queries
- [ ] USDC on Base for payment
- [ ] Earning reputation = free (subsidized gas or agent-pays)
- [ ] Querying reputation = paid (platforms pay per lookup)

---

## How to Test (Current State)

### Unit tests (no blockchain needed)
```bash
pip install pytest
python -m pytest tests/test_agentscore.py -v
```

### Manual integration test (requires testnet)
```bash
# 1. Install web3
pip install web3

# 2. Enable in config (~/.hermes/config.yaml)
#    agentscore:
#      enabled: true
#      network: base-sepolia

# 3. Deploy contract and set address
export AGENTSCORE_CONTRACT=0x...

# 4. Fund agent wallet (address printed on first gateway start)
# Faucet: https://www.coinbase.com/faucets/base-ethereum-goerli-faucet

# 5. Start gateway
hermes gateway run --replace

# 6. Send messages — each tool-using interaction gets attested
# 7. Check BaseScan for attestation transactions
```

### Offline mode test
```bash
# Just enable without web3 installed — should log "(offline)" for each interaction
# and gateway should work normally
```

---

## Vision

AgentScore becomes the **portable reputation layer for AI agents**:

1. **Agents earn reputation** by doing real work — every tool call, every completion, every error is recorded
2. **Reputation is unfakeable** — attested on-chain from gateway-observed telemetry, not self-reported
3. **Reputation is portable** — follows the agent across platforms, frameworks, and providers
4. **Platforms use reputation to gate access** — marketplaces require minimum scores to list
5. **Model accountability** — on-chain model hashes create a public record of which models power which agents
6. **Token economics are transparent** — input/output token usage is publicly trackable per agent

The endgame: when a user interacts with an AI agent, they can check its on-chain score the same way they'd check a credit score — instantly, verifiably, and without trusting any single platform.

---

## Goals (Milestones)

| Milestone | Target | Success Criteria |
|-----------|--------|-----------------|
| **M1**: Contract deployed | Week 1 | Contract verified on Base Sepolia, first attestation lands |
| **M2**: E2E working | Week 2 | Gateway attests interactions, score queryable on-chain |
| **M3**: Reaper + hardening | Week 3 | Stale reaper scheduled, all edge cases tested, 40+ tests |
| **M4**: Score CLI | Week 4 | `hermes agentscore` shows score, count, model breakdown |
| **M5**: Mainnet deploy | Week 6 | Contract on Base mainnet, real attestations flowing |
| **M6**: x402 integration | Week 8+ | Pay-per-query score API live |
