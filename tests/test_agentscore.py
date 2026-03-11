"""Tests for the AgentScore tracking system (SDK-based)."""

import time
from unittest.mock import MagicMock, patch

import pytest

from agentscore.tracker import InteractionTracker, setup_shutdown_handler


@pytest.fixture
def mock_sdk():
    """Create a mock SDK client."""
    sdk = MagicMock()
    sdk.attest_batch.return_value = {"txHash": "0xabc123", "agent": "0x1234", "count": "1"}
    sdk.register.return_value = {"txHash": "0xdef456", "agent": "0x1234"}
    sdk.get_profile.return_value = {"address": "0x1234", "score": 0}
    return sdk


class TestTrackerStateMachine:
    """Test the tracker state machine: start -> step -> end lifecycle."""

    def test_start_creates_interaction(self, mock_sdk):
        tracker = InteractionTracker(sdk_client=mock_sdk)
        tracker.on_start({"session_id": "test-session-1"})

        assert "test-session-1" in tracker._interactions
        assert tracker._interactions["test-session-1"]["started_at"] > 0

    def test_step_increments_tool_calls(self, mock_sdk):
        tracker = InteractionTracker(sdk_client=mock_sdk)
        tracker.on_start({"session_id": "test-session-1"})

        tracker.on_step({
            "session_id": "test-session-1",
            "iteration": 1,
            "tool_names": ["terminal", "file_read"],
        })

        interaction = tracker._interactions["test-session-1"]
        assert interaction["total_tool_calls"] == 2
        assert interaction["iterations"] == 1

    def test_multiple_steps_accumulate(self, mock_sdk):
        tracker = InteractionTracker(sdk_client=mock_sdk)
        tracker.on_start({"session_id": "test-session-1"})

        tracker.on_step({
            "session_id": "test-session-1",
            "iteration": 1,
            "tool_names": ["terminal"],
        })
        tracker.on_step({
            "session_id": "test-session-1",
            "iteration": 2,
            "tool_names": ["file_read", "memory"],
        })

        interaction = tracker._interactions["test-session-1"]
        assert interaction["total_tool_calls"] == 3
        assert interaction["iterations"] == 2

    def test_end_removes_interaction(self, mock_sdk):
        tracker = InteractionTracker(sdk_client=mock_sdk)
        tracker.on_start({"session_id": "test-session-1"})
        tracker.on_end({"session_id": "test-session-1"})

        assert "test-session-1" not in tracker._interactions

    def test_full_lifecycle(self, mock_sdk):
        tracker = InteractionTracker(sdk_client=mock_sdk)

        tracker.on_start({"session_id": "test-session-1"})
        tracker.on_step({
            "session_id": "test-session-1",
            "iteration": 1,
            "tool_names": ["terminal"],
        })
        tracker.on_step({
            "session_id": "test-session-1",
            "iteration": 2,
            "tool_names": ["file_read"],
        })
        tracker.on_end({"session_id": "test-session-1"})

        assert tracker._stats["tracked"] == 1

    def test_late_step_creates_interaction(self, mock_sdk):
        """Step without a prior start should auto-create the interaction."""
        tracker = InteractionTracker(sdk_client=mock_sdk)
        tracker.on_step({
            "session_id": "late-session",
            "iteration": 1,
            "tool_names": ["terminal"],
        })

        assert "late-session" in tracker._interactions
        assert tracker._interactions["late-session"]["total_tool_calls"] == 1


class TestTrackerSkipsTrivial:
    """Test that tracker skips trivial interactions with 0 tool calls."""

    def test_skips_zero_tool_calls(self, mock_sdk):
        tracker = InteractionTracker(sdk_client=mock_sdk)
        tracker.on_start({"session_id": "test-session-1"})
        tracker.on_end({"session_id": "test-session-1"})

        assert tracker._stats["skipped"] == 1
        assert tracker._stats["tracked"] == 1
        assert not mock_sdk.attest_batch.called


class TestToolErrorDetection:
    """Test tool error detection from step events."""

    def test_tool_errors_tracked(self, mock_sdk):
        tracker = InteractionTracker(sdk_client=mock_sdk)
        tracker.on_start({"session_id": "s1"})
        tracker.on_step({
            "session_id": "s1",
            "iteration": 1,
            "tool_names": ["terminal"],
            "tool_errors": 1,
        })

        assert tracker._interactions["s1"]["total_tool_errors"] == 1

    def test_tool_errors_accumulate(self, mock_sdk):
        tracker = InteractionTracker(sdk_client=mock_sdk)
        tracker.on_start({"session_id": "s1"})

        tracker.on_step({
            "session_id": "s1",
            "iteration": 1,
            "tool_names": ["terminal"],
            "tool_errors": 1,
        })
        tracker.on_step({
            "session_id": "s1",
            "iteration": 2,
            "tool_names": ["file_read"],
            "tool_errors": 2,
        })

        assert tracker._interactions["s1"]["total_tool_errors"] == 3


class TestBatchAccumulation:
    """Test batch accumulation and flush threshold logic."""

    def test_batch_flushes_on_threshold(self, mock_sdk):
        tracker = InteractionTracker(sdk_client=mock_sdk)
        tracker._batch_size_threshold = 2

        for i in range(2):
            tracker.on_start({"session_id": f"session-{i}"})
            tracker.on_step({
                "session_id": f"session-{i}",
                "iteration": 1,
                "tool_names": ["terminal"],
            })
            tracker.on_end({"session_id": f"session-{i}"})

        assert mock_sdk.attest_batch.called

    def test_flush_on_time_threshold(self, mock_sdk):
        tracker = InteractionTracker(sdk_client=mock_sdk)
        tracker._batch_size_threshold = 100
        tracker._batch_time_threshold = 0  # Immediate time flush

        tracker.on_start({"session_id": "session-0"})
        tracker.on_step({
            "session_id": "session-0",
            "iteration": 1,
            "tool_names": ["terminal"],
        })
        tracker.on_end({"session_id": "session-0"})

        assert mock_sdk.attest_batch.called

    def test_attestation_contains_correct_data(self, mock_sdk):
        tracker = InteractionTracker(sdk_client=mock_sdk)
        tracker._batch_size_threshold = 1

        tracker.on_start({"session_id": "s1", "model": "claude-opus-4"})
        tracker.on_step({
            "session_id": "s1",
            "iteration": 1,
            "tool_names": ["terminal", "file_read"],
            "tool_errors": 1,
            "input_tokens": 500,
            "output_tokens": 200,
        })
        tracker.on_end({"session_id": "s1"})

        call_args = mock_sdk.attest_batch.call_args
        assert call_args is not None
        attestations = call_args[1]["attestations"] if "attestations" in (call_args[1] or {}) else call_args[0][0]
        att = attestations[0]
        assert att["tool_calls"] == 2
        assert att["tool_errors"] == 1
        assert att["completed"] is True
        assert att["input_tokens"] == 500
        assert att["output_tokens"] == 200
        assert att["model_hash"].startswith("0x")
        assert att["metrics_hash"].startswith("0x")


class TestCompletedFlag:
    """Test that completed status is correctly derived from context."""

    def test_completed_true_by_default(self, mock_sdk):
        tracker = InteractionTracker(sdk_client=mock_sdk)
        tracker._batch_size_threshold = 1

        tracker.on_start({"session_id": "s1"})
        tracker.on_step({"session_id": "s1", "iteration": 1, "tool_names": ["terminal"]})
        tracker.on_end({"session_id": "s1"})

        att = mock_sdk.attest_batch.call_args[0][0][0]
        assert att["completed"] is True

    def test_completed_false_on_error(self, mock_sdk):
        tracker = InteractionTracker(sdk_client=mock_sdk)
        tracker._batch_size_threshold = 1

        tracker.on_start({"session_id": "s1"})
        tracker.on_step({"session_id": "s1", "iteration": 1, "tool_names": ["terminal"]})
        tracker.on_end({"session_id": "s1", "error": "budget exhausted"})

        att = mock_sdk.attest_batch.call_args[0][0][0]
        assert att["completed"] is False

    def test_completed_explicit_false(self, mock_sdk):
        tracker = InteractionTracker(sdk_client=mock_sdk)
        tracker._batch_size_threshold = 1

        tracker.on_start({"session_id": "s1"})
        tracker.on_step({"session_id": "s1", "iteration": 1, "tool_names": ["terminal"]})
        tracker.on_end({"session_id": "s1", "completed": False})

        att = mock_sdk.attest_batch.call_args[0][0][0]
        assert att["completed"] is False


class TestStaleReaping:
    """Test that stale interactions are reaped as incomplete."""

    def test_reap_stale_interactions(self, mock_sdk):
        tracker = InteractionTracker(sdk_client=mock_sdk)

        tracker.on_start({"session_id": "s1"})
        tracker.on_step({"session_id": "s1", "iteration": 1, "tool_names": ["terminal"]})
        tracker._interactions["s1"]["started_at"] = time.time() - 7200

        reaped = tracker.reap_stale(max_age=3600)
        assert reaped == 1
        assert "s1" not in tracker._interactions
        assert tracker._stats["tracked"] == 1

    def test_reap_does_not_touch_fresh(self, mock_sdk):
        tracker = InteractionTracker(sdk_client=mock_sdk)
        tracker.on_start({"session_id": "s1"})
        tracker.on_step({"session_id": "s1", "iteration": 1, "tool_names": ["terminal"]})

        reaped = tracker.reap_stale(max_age=3600)
        assert reaped == 0
        assert "s1" in tracker._interactions


class TestModelAndTokenTracking:
    """Test model identification and token usage tracking."""

    def test_model_tracked_from_start(self, mock_sdk):
        tracker = InteractionTracker(sdk_client=mock_sdk)
        tracker.on_start({"session_id": "s1", "model": "claude-opus-4"})

        assert tracker._interactions["s1"]["model"] == "claude-opus-4"

    def test_model_updated_from_step(self, mock_sdk):
        tracker = InteractionTracker(sdk_client=mock_sdk)
        tracker.on_start({"session_id": "s1", "model": "model-a"})
        tracker.on_step({
            "session_id": "s1", "iteration": 1,
            "tool_names": ["terminal"], "model": "model-b",
        })

        assert tracker._interactions["s1"]["model"] == "model-b"

    def test_tokens_accumulate_across_steps(self, mock_sdk):
        tracker = InteractionTracker(sdk_client=mock_sdk)
        tracker.on_start({"session_id": "s1"})
        tracker.on_step({
            "session_id": "s1", "iteration": 1,
            "tool_names": ["terminal"],
            "input_tokens": 500, "output_tokens": 200,
        })
        tracker.on_step({
            "session_id": "s1", "iteration": 2,
            "tool_names": ["file_read"],
            "input_tokens": 800, "output_tokens": 300,
        })

        interaction = tracker._interactions["s1"]
        assert interaction["input_tokens"] == 1300
        assert interaction["output_tokens"] == 500

    def test_zero_tokens_when_not_provided(self, mock_sdk):
        tracker = InteractionTracker(sdk_client=mock_sdk)
        tracker._batch_size_threshold = 1

        tracker.on_start({"session_id": "s1"})
        tracker.on_step({"session_id": "s1", "iteration": 1, "tool_names": ["terminal"]})
        tracker.on_end({"session_id": "s1"})

        att = mock_sdk.attest_batch.call_args[0][0][0]
        assert att["input_tokens"] == 0
        assert att["output_tokens"] == 0


class TestOfflineMode:
    """Test offline/graceful degradation mode."""

    def test_offline_mode_no_attestation(self):
        tracker = InteractionTracker(sdk_client=None)

        tracker.on_start({"session_id": "session-0"})
        tracker.on_step({
            "session_id": "session-0",
            "iteration": 1,
            "tool_names": ["terminal"],
        })
        tracker.on_end({"session_id": "session-0"})

        assert tracker._stats["tracked"] == 1
        assert tracker._stats["attested"] == 0


class TestFlushMethod:
    """Test the flush method."""

    def test_flush_clears_pending(self, mock_sdk):
        tracker = InteractionTracker(sdk_client=mock_sdk)

        tracker._pending_attestations.append({
            "tool_calls": 1,
            "tool_errors": 0,
            "duration": 10,
            "completed": True,
            "model_hash": "0x1234",
            "input_tokens": 100,
            "output_tokens": 200,
            "metrics_hash": "0x5678",
        })

        tracker.flush()
        assert len(tracker._pending_attestations) == 0
        assert mock_sdk.attest_batch.called

    def test_flush_on_shutdown(self, mock_sdk):
        tracker = InteractionTracker(sdk_client=mock_sdk)
        setup_shutdown_handler(tracker)
        assert tracker is not None

    def test_flush_handles_sdk_error(self, mock_sdk):
        mock_sdk.attest_batch.side_effect = Exception("Server down")
        tracker = InteractionTracker(sdk_client=mock_sdk)

        tracker._pending_attestations.append({
            "tool_calls": 1,
            "tool_errors": 0,
            "duration": 10,
            "completed": True,
            "model_hash": "0x1234",
            "input_tokens": 100,
            "output_tokens": 200,
            "metrics_hash": "0x5678",
        })

        tracker.flush()
        assert tracker._stats["errors"] == 1
        assert len(tracker._pending_attestations) == 0
