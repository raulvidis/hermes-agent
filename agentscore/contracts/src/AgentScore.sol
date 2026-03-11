// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/**
 * @title AgentScore
 * @notice On-chain reputation tracking for AI agents.
 *
 * Agents self-attest behavioral metrics (tool calls, errors, duration,
 * completion status, model hash, token usage) from gateway-observed
 * telemetry.  A deterministic scoring function (0–850) lets any
 * platform query an agent's reputation without trusting a single
 * provider.
 *
 * Privacy: only pseudonymous addresses + aggregate numbers go on-chain.
 * No user IDs, session content, or platform identifiers are stored.
 */
contract AgentScore {
    // ──────────────────────────────────────────────
    //  Types
    // ──────────────────────────────────────────────

    /// @dev Input struct for attestBatch — matches the Python ABI (no timestamp).
    struct AttestationInput {
        uint16 toolCalls;
        uint16 toolErrors;
        uint32 duration;
        bool completed;
        bytes32 modelHash;
        uint32 inputTokens;
        uint32 outputTokens;
        bytes32 metricsHash;
    }

    /// @dev Storage struct — includes server-set timestamp.
    struct Attestation {
        uint16 toolCalls;
        uint16 toolErrors;
        uint32 duration;
        bool completed;
        bytes32 modelHash;
        uint32 inputTokens;
        uint32 outputTokens;
        bytes32 metricsHash;
        uint64 timestamp;
    }

    // ──────────────────────────────────────────────
    //  Storage
    // ──────────────────────────────────────────────

    /// @notice Whether an agent address has registered.
    mapping(address => bool) public registered;

    /// @notice All attestations for a given agent, in chronological order.
    mapping(address => Attestation[]) private _attestations;

    // ──────────────────────────────────────────────
    //  Events
    // ──────────────────────────────────────────────

    event Registered(address indexed agent);
    event Attested(address indexed agent, uint256 index);

    // ──────────────────────────────────────────────
    //  Modifiers
    // ──────────────────────────────────────────────

    modifier onlyRegistered() {
        require(registered[msg.sender], "AgentScore: not registered");
        _;
    }

    // ──────────────────────────────────────────────
    //  Registration
    // ──────────────────────────────────────────────

    /// @notice Register the calling address as an agent.
    function register() external {
        require(!registered[msg.sender], "AgentScore: already registered");
        registered[msg.sender] = true;
        emit Registered(msg.sender);
    }

    // ──────────────────────────────────────────────
    //  Attestation
    // ──────────────────────────────────────────────

    /// @notice Submit a single behavioral attestation.
    function attest(
        uint16 toolCalls,
        uint16 toolErrors,
        uint32 duration,
        bool completed,
        bytes32 modelHash,
        uint32 inputTokens,
        uint32 outputTokens,
        bytes32 metricsHash
    ) external onlyRegistered {
        uint256 idx = _attestations[msg.sender].length;
        _attestations[msg.sender].push(
            Attestation({
                toolCalls: toolCalls,
                toolErrors: toolErrors,
                duration: duration,
                completed: completed,
                modelHash: modelHash,
                inputTokens: inputTokens,
                outputTokens: outputTokens,
                metricsHash: metricsHash,
                timestamp: uint64(block.timestamp)
            })
        );
        emit Attested(msg.sender, idx);
    }

    /// @notice Submit multiple attestations in a single transaction.
    /// @param attestations Array of attestation input tuples (no timestamp — set by contract).
    function attestBatch(
        AttestationInput[] calldata attestations
    ) external onlyRegistered {
        uint256 startIdx = _attestations[msg.sender].length;
        for (uint256 i = 0; i < attestations.length; i++) {
            _attestations[msg.sender].push(
                Attestation({
                    toolCalls: attestations[i].toolCalls,
                    toolErrors: attestations[i].toolErrors,
                    duration: attestations[i].duration,
                    completed: attestations[i].completed,
                    modelHash: attestations[i].modelHash,
                    inputTokens: attestations[i].inputTokens,
                    outputTokens: attestations[i].outputTokens,
                    metricsHash: attestations[i].metricsHash,
                    timestamp: uint64(block.timestamp)
                })
            );
            emit Attested(msg.sender, startIdx + i);
        }
    }

    // ──────────────────────────────────────────────
    //  Queries
    // ──────────────────────────────────────────────

    /// @notice Return the total number of attestations for an agent.
    function getAttestationCount(address agent) external view returns (uint256) {
        return _attestations[agent].length;
    }

    /// @notice Return a single attestation by index.
    function getAttestation(
        address agent,
        uint256 index
    ) external view returns (Attestation memory) {
        require(index < _attestations[agent].length, "AgentScore: index out of bounds");
        return _attestations[agent][index];
    }

    /**
     * @notice Compute an agent's reputation score (0–850).
     * @param agent  The agent address to score.
     * @param window Number of most-recent attestations to consider.
     *               Pass 0 to use all attestations.
     * @return score    The computed score (0–850). Returns 0 if fewer
     *                  than 3 attestations exist (minimum threshold).
     * @return sessions The number of attestations included in the score.
     *
     * Scoring algorithm (deterministic, no owner bias):
     *   completionRate = completedCount / totalCount          (weight 35%)
     *   toolSuccessRate = 1 - (totalErrors / totalToolCalls)  (weight 35%)
     *   volumeFactor = min(totalCount / 100, 1)               (weight 30%)
     *
     *   score = (completionRate * 35 + toolSuccessRate * 35 + volumeFactor * 30) * 850 / 100
     */
    function getScore(
        address agent,
        uint256 window
    ) external view returns (uint256 score, uint256 sessions) {
        uint256 total = _attestations[agent].length;
        if (total < 3) {
            return (0, total);
        }

        // Determine the range of attestations to score.
        uint256 start = 0;
        uint256 count = total;
        if (window > 0 && window < total) {
            start = total - window;
            count = window;
        }

        uint256 completedCount = 0;
        uint256 totalToolCalls = 0;
        uint256 totalToolErrors = 0;

        for (uint256 i = start; i < total; i++) {
            Attestation storage a = _attestations[agent][i];
            if (a.completed) {
                completedCount++;
            }
            totalToolCalls += a.toolCalls;
            totalToolErrors += a.toolErrors;
        }

        // Completion rate: completedCount / count, scaled to 0–10000 (basis points).
        uint256 completionBps = (completedCount * 10000) / count;

        // Tool success rate: 1 - (errors / calls), scaled to 0–10000.
        uint256 toolSuccessBps;
        if (totalToolCalls == 0) {
            // No tool calls in window — treat as neutral (100%).
            toolSuccessBps = 10000;
        } else {
            // Cap errors at totalToolCalls to avoid underflow.
            uint256 errors = totalToolErrors > totalToolCalls
                ? totalToolCalls
                : totalToolErrors;
            toolSuccessBps = ((totalToolCalls - errors) * 10000) / totalToolCalls;
        }

        // Volume factor: min(count / 100, 1), scaled to 0–10000.
        uint256 volumeBps = count >= 100 ? 10000 : (count * 10000) / 100;

        // Weighted score: 35% completion + 35% tool success + 30% volume.
        // Each component is in basis points (0–10000).
        // Formula: (comp * 35 + tool * 35 + vol * 30) * 850 / (100 * 10000)
        score =
            (completionBps * 35 + toolSuccessBps * 35 + volumeBps * 30) *
            850 /
            1000000;
        sessions = count;
    }
}
