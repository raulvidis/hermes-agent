#!/usr/bin/env bash
# Deploy AgentScore to Base Sepolia
#
# Prerequisites:
#   1. Fund the deployer wallet with Base Sepolia ETH
#      Faucet: https://www.coinbase.com/faucets/base-ethereum-goerli-faucet
#      or:     https://faucet.quicknode.com/base/sepolia
#
#   2. Set your deployer private key:
#      export DEPLOYER_PRIVATE_KEY=0x...
#
# Usage:
#   cd agentscore/contracts
#   bash script/deploy.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONTRACT_DIR="$(dirname "$SCRIPT_DIR")"
AGENTSCORE_DIR="$HOME/.agentscore"

if [ -z "${DEPLOYER_PRIVATE_KEY:-}" ]; then
    echo "Error: DEPLOYER_PRIVATE_KEY not set"
    echo ""
    echo "  export DEPLOYER_PRIVATE_KEY=0x..."
    echo ""
    echo "Fund your wallet at: https://www.coinbase.com/faucets/base-ethereum-goerli-faucet"
    exit 1
fi

echo "==> Deploying AgentScore to Base Sepolia..."

cd "$CONTRACT_DIR"

# Deploy and capture output
OUTPUT=$(forge script script/Deploy.s.sol:DeployAgentScore \
    --rpc-url https://sepolia.base.org \
    --broadcast \
    --verify \
    --etherscan-api-key "${BASESCAN_API_KEY:-}" \
    2>&1) || {
    echo "$OUTPUT"
    echo ""
    echo "Deployment failed. Check that your wallet has Base Sepolia ETH."
    exit 1
}

echo "$OUTPUT"

# Extract deployed address from forge output
CONTRACT_ADDRESS=$(echo "$OUTPUT" | grep -oP '(?<=AgentScore deployed at: )0x[a-fA-F0-9]{40}' || true)

if [ -z "$CONTRACT_ADDRESS" ]; then
    # Try alternative extraction from broadcast artifacts
    CONTRACT_ADDRESS=$(cat broadcast/Deploy.s.sol/84532/run-latest.json 2>/dev/null \
        | python3 -c "import sys,json; txs=json.load(sys.stdin)['transactions']; print(next(t['contractAddress'] for t in txs if t.get('contractAddress')))" 2>/dev/null || true)
fi

if [ -n "$CONTRACT_ADDRESS" ]; then
    echo ""
    echo "==> Contract deployed at: $CONTRACT_ADDRESS"
    echo "==> Explorer: https://sepolia.basescan.org/address/$CONTRACT_ADDRESS"

    # Save to ~/.agentscore/contract.json
    mkdir -p "$AGENTSCORE_DIR"
    python3 -c "
import json, os
path = '$AGENTSCORE_DIR/contract.json'
data = {}
if os.path.exists(path):
    data = json.loads(open(path).read())
data['base-sepolia'] = {'address': '$CONTRACT_ADDRESS', 'network': 'base-sepolia', 'chain_id': 84532}
open(path, 'w').write(json.dumps(data, indent=2))
print('Saved to', path)
"
else
    echo ""
    echo "==> Could not extract contract address from output."
    echo "    Check the broadcast artifacts in: broadcast/Deploy.s.sol/84532/"
    echo "    Then manually set: export AGENTSCORE_CONTRACT=0x..."
fi
