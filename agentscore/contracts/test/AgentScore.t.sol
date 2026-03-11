// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {AgentScore} from "../src/AgentScore.sol";

contract AgentScoreTest is Test {
    AgentScore public score;
    address public agent1;
    address public agent2;

    function setUp() public {
        score = new AgentScore();
        agent1 = makeAddr("agent1");
        agent2 = makeAddr("agent2");
    }

    // ── Registration ──────────────────────────────

    function test_register() public {
        vm.prank(agent1);
        score.register();
        assertTrue(score.registered(agent1));
    }

    function test_register_twice_reverts() public {
        vm.prank(agent1);
        score.register();

        vm.prank(agent1);
        vm.expectRevert("AgentScore: already registered");
        score.register();
    }

    function test_registered_emits_event() public {
        vm.prank(agent1);
        vm.expectEmit(true, false, false, false);
        emit AgentScore.Registered(agent1);
        score.register();
    }

    // ── Attestation ───────────────────────────────

    function test_attest_requires_registration() public {
        vm.prank(agent1);
        vm.expectRevert("AgentScore: not registered");
        score.attest(5, 0, 60, true, bytes32(0), 100, 200, bytes32(0));
    }

    function test_attest_single() public {
        vm.prank(agent1);
        score.register();

        vm.prank(agent1);
        score.attest(5, 1, 120, true, keccak256("claude-opus-4"), 500, 1000, keccak256("metrics"));

        assertEq(score.getAttestationCount(agent1), 1);
    }

    function test_attest_emits_event() public {
        vm.prank(agent1);
        score.register();

        vm.prank(agent1);
        vm.expectEmit(true, false, false, true);
        emit AgentScore.Attested(agent1, 0);
        score.attest(5, 0, 60, true, bytes32(0), 100, 200, bytes32(0));
    }

    function test_getAttestation() public {
        vm.prank(agent1);
        score.register();

        vm.prank(agent1);
        score.attest(10, 2, 300, true, keccak256("gpt-4"), 1000, 2000, keccak256("m1"));

        AgentScore.Attestation memory a = score.getAttestation(agent1, 0);
        assertEq(a.toolCalls, 10);
        assertEq(a.toolErrors, 2);
        assertEq(a.duration, 300);
        assertTrue(a.completed);
        assertEq(a.inputTokens, 1000);
        assertEq(a.outputTokens, 2000);
    }

    function test_getAttestation_out_of_bounds() public {
        vm.expectRevert("AgentScore: index out of bounds");
        score.getAttestation(agent1, 0);
    }

    // ── Batch Attestation ─────────────────────────

    function test_attestBatch() public {
        vm.prank(agent1);
        score.register();

        AgentScore.AttestationInput[] memory batch = new AgentScore.AttestationInput[](3);
        for (uint256 i = 0; i < 3; i++) {
            batch[i] = AgentScore.AttestationInput({
                toolCalls: uint16(5 + i),
                toolErrors: 0,
                duration: uint32(60 + i * 30),
                completed: true,
                modelHash: keccak256("claude-opus-4"),
                inputTokens: uint32(100 * (i + 1)),
                outputTokens: uint32(200 * (i + 1)),
                metricsHash: keccak256(abi.encodePacked("metrics", i))
            });
        }

        vm.prank(agent1);
        score.attestBatch(batch);

        assertEq(score.getAttestationCount(agent1), 3);
    }

    function test_attestBatch_requires_registration() public {
        AgentScore.AttestationInput[] memory batch = new AgentScore.AttestationInput[](1);
        batch[0] = _makeAttestationInput(5, 0, 60, true);

        vm.prank(agent1);
        vm.expectRevert("AgentScore: not registered");
        score.attestBatch(batch);
    }

    // ── Scoring ───────────────────────────────────

    function test_score_below_threshold_returns_zero() public {
        vm.prank(agent1);
        score.register();

        // Only 2 attestations — below the 3 minimum
        vm.prank(agent1);
        score.attest(5, 0, 60, true, bytes32(0), 100, 200, bytes32(0));
        vm.prank(agent1);
        score.attest(5, 0, 60, true, bytes32(0), 100, 200, bytes32(0));

        (uint256 s, uint256 sessions) = score.getScore(agent1, 0);
        assertEq(s, 0);
        assertEq(sessions, 2);
    }

    function test_score_perfect_3_sessions() public {
        _registerAndAttest(agent1, 3, 5, 0, true);

        (uint256 s, uint256 sessions) = score.getScore(agent1, 0);
        assertEq(sessions, 3);
        // completion=100%, toolSuccess=100%, volume=3/100=3%
        // (10000*35 + 10000*35 + 300*30) * 850 / 1000000
        // = (350000 + 350000 + 9000) * 850 / 1000000
        // = 709000 * 850 / 1000000 = 602
        assertEq(s, 602);
    }

    function test_score_perfect_100_sessions() public {
        _registerAndAttest(agent1, 100, 5, 0, true);

        (uint256 s, uint256 sessions) = score.getScore(agent1, 0);
        assertEq(sessions, 100);
        // completion=100%, toolSuccess=100%, volume=100%
        // (10000*35 + 10000*35 + 10000*30) * 850 / 1000000
        // = 1000000 * 850 / 1000000 = 850
        assertEq(s, 850);
    }

    function test_score_all_failed() public {
        _registerAndAttest(agent1, 5, 5, 5, false);

        (uint256 s, uint256 sessions) = score.getScore(agent1, 0);
        assertEq(sessions, 5);
        // completion=0%, toolSuccess=0%, volume=5%
        // (0 + 0 + 500*30) * 850 / 1000000
        // = 15000 * 850 / 1000000 = 12
        assertEq(s, 12);
    }

    function test_score_with_window() public {
        vm.prank(agent1);
        score.register();

        // First 3: all failed
        for (uint256 i = 0; i < 3; i++) {
            vm.prank(agent1);
            score.attest(10, 10, 60, false, bytes32(0), 100, 200, bytes32(0));
        }
        // Next 5: all perfect
        for (uint256 i = 0; i < 5; i++) {
            vm.prank(agent1);
            score.attest(10, 0, 60, true, bytes32(0), 100, 200, bytes32(0));
        }

        // Window=5 should only score the last 5 (perfect) attestations
        (uint256 windowed, uint256 wSessions) = score.getScore(agent1, 5);
        assertEq(wSessions, 5);

        // Window=0 scores all 8
        (uint256 full, uint256 fSessions) = score.getScore(agent1, 0);
        assertEq(fSessions, 8);

        // Windowed score should be higher than full score
        assertGt(windowed, full);
    }

    function test_score_mixed_results() public {
        vm.prank(agent1);
        score.register();

        // 7 completed, 3 not. 50 tool calls, 10 errors. 10 sessions.
        for (uint256 i = 0; i < 10; i++) {
            vm.prank(agent1);
            score.attest(
                5,
                i < 2 ? 5 : 0, // errors in first 2 sessions
                120,
                i < 7,          // first 7 completed
                bytes32(0),
                100,
                200,
                bytes32(0)
            );
        }

        (uint256 s, uint256 sessions) = score.getScore(agent1, 0);
        assertEq(sessions, 10);
        // completion=7/10=70%, toolSuccess=(50-10)/50=80%, volume=10%
        // (7000*35 + 8000*35 + 1000*30) * 850 / 1000000
        // = (245000 + 280000 + 30000) * 850 / 1000000
        // = 555000 * 850 / 1000000 = 471
        assertEq(s, 471);
    }

    function test_score_unregistered_agent() public {
        (uint256 s, uint256 sessions) = score.getScore(agent1, 0);
        assertEq(s, 0);
        assertEq(sessions, 0);
    }

    function test_score_errors_exceed_calls() public {
        // Edge case: tool_errors > tool_calls (shouldn't happen, but contract handles it)
        vm.prank(agent1);
        score.register();

        for (uint256 i = 0; i < 3; i++) {
            vm.prank(agent1);
            score.attest(2, 5, 60, true, bytes32(0), 100, 200, bytes32(0));
        }

        (uint256 s, ) = score.getScore(agent1, 0);
        // toolSuccessBps should be 0, not underflow
        // completion=100%, toolSuccess=0%, volume=3%
        // (10000*35 + 0 + 300*30) * 850 / 1000000
        // = (350000 + 9000) * 850 / 1000000 = 305 (integer math)
        assertEq(s, 305);
    }

    function test_window_larger_than_total() public {
        _registerAndAttest(agent1, 5, 5, 0, true);

        // Window of 100 but only 5 attestations — should use all 5
        (uint256 s1, uint256 sess1) = score.getScore(agent1, 100);
        (uint256 s2, uint256 sess2) = score.getScore(agent1, 0);
        assertEq(s1, s2);
        assertEq(sess1, sess2);
    }

    // ── Multiple agents ───────────────────────────

    function test_independent_agents() public {
        _registerAndAttest(agent1, 5, 10, 0, true);
        _registerAndAttest(agent2, 5, 10, 10, false);

        (uint256 s1, ) = score.getScore(agent1, 0);
        (uint256 s2, ) = score.getScore(agent2, 0);

        assertGt(s1, s2);
    }

    // ── Helpers ───────────────────────────────────

    function _makeAttestationInput(
        uint16 toolCalls,
        uint16 toolErrors,
        uint32 duration,
        bool completed
    ) internal pure returns (AgentScore.AttestationInput memory) {
        return AgentScore.AttestationInput({
            toolCalls: toolCalls,
            toolErrors: toolErrors,
            duration: duration,
            completed: completed,
            modelHash: bytes32(0),
            inputTokens: 100,
            outputTokens: 200,
            metricsHash: bytes32(0)
        });
    }

    function _registerAndAttest(
        address agent,
        uint256 count,
        uint16 toolCalls,
        uint16 toolErrors,
        bool completed
    ) internal {
        vm.prank(agent);
        score.register();

        for (uint256 i = 0; i < count; i++) {
            vm.prank(agent);
            score.attest(
                toolCalls,
                toolErrors,
                60,
                completed,
                bytes32(0),
                100,
                200,
                keccak256(abi.encodePacked("metrics", i))
            );
        }
    }
}
