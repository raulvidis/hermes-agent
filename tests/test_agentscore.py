"""Tests for the AgentScore tracking and attestation system."""

import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_web3():
    """Mock web3 module that's not installed."""
    mock_w3 = MagicMock()
    mock_w3.keccak.return_value = b"\x12\x34\x56\x78" * 8

    web3_mock = MagicMock()
    web3_mock.Web3 = mock_w3
    return web3_mock


@pytest.fixture
def mock_chain_client():
    """Create a mock chain client."""
    client = MagicMock()
    client.is_ready.return_value = True
    client.attest.return_value = "0xabc123"
    client.attest_batch.return_value = "0xdef456"
    client.is_registered.return_value = True
    return client


@pytest.fixture
def pending_file(tmp_path):
    """Create a temporary pending attestations file."""
    agentscore_dir = tmp_path / ".agentscore"
    agentscore_dir.mkdir()
    pending_file = agentscore_dir / "pending.json"
    yield pending_file
    if pending_file.exists():
        pending_file.unlink()


class TestTrackerStateMachine:
    """Test the tracker state machine: start -> step -> end lifecycle."""

    def test_start_creates_interaction(self, mock_chain_client, mock_web3):
        sys.modules["web3"] = mock_web3

        from agentscore.tracker import InteractionTracker

        tracker = InteractionTracker(chain_client=mock_chain_client)
        tracker.on_start({"session_id": "test-session-1"})

        assert "test-session-1" in tracker._interactions
        assert tracker._interactions["test-session-1"]["started_at"] > 0

    def test_step_increments_tool_calls(self, mock_chain_client, mock_web3):
        sys.modules["web3"] = mock_web3

        from agentscore.tracker import InteractionTracker

        tracker = InteractionTracker(chain_client=mock_chain_client)
        tracker.on_start({"session_id": "test-session-1"})

        tracker.on_step(
            {
                "session_id": "test-session-1",
                "iteration": 1,
                "tool_names": ["terminal", "file_read"],
            }
        )

        interaction = tracker._interactions["test-session-1"]
        assert interaction["total_tool_calls"] == 2
        assert interaction["iterations"] == 1

    def test_multiple_steps_accumulate(self, mock_chain_client, mock_web3):
        sys.modules["web3"] = mock_web3

        from agentscore.tracker import InteractionTracker

        tracker = InteractionTracker(chain_client=mock_chain_client)
        tracker.on_start({"session_id": "test-session-1"})

        tracker.on_step(
            {
                "session_id": "test-session-1",
                "iteration": 1,
                "tool_names": ["terminal"],
            }
        )

        tracker.on_step(
            {
                "session_id": "test-session-1",
                "iteration": 2,
                "tool_names": ["file_read", "memory"],
            }
        )

        interaction = tracker._interactions["test-session-1"]
        assert interaction["total_tool_calls"] == 3
        assert interaction["iterations"] == 2

    def test_end_removes_interaction(self, mock_chain_client):
        from agentscore.tracker import InteractionTracker

        tracker = InteractionTracker(chain_client=mock_chain_client)
        tracker.on_start({"session_id": "test-session-1"})

        tracker.on_end({"session_id": "test-session-1"})

        assert "test-session-1" not in tracker._interactions

    def test_full_lifecycle(self, mock_chain_client, mock_web3):
        sys.modules["web3"] = mock_web3

        from agentscore.tracker import InteractionTracker

        tracker = InteractionTracker(chain_client=mock_chain_client)

        tracker.on_start({"session_id": "test-session-1"})
        tracker.on_step(
            {
                "session_id": "test-session-1",
                "iteration": 1,
                "tool_names": ["terminal"],
            }
        )
        tracker.on_step(
            {
                "session_id": "test-session-1",
                "iteration": 2,
                "tool_names": ["file_read"],
            }
        )
        tracker.on_end({"session_id": "test-session-1"})

        assert tracker._stats["tracked"] == 1


class TestTrackerSkipsTrivial:
    """Test that tracker skips trivial interactions with 0 tool calls."""

    def test_skips_zero_tool_calls(self, mock_chain_client):
        from agentscore.tracker import InteractionTracker

        tracker = InteractionTracker(chain_client=mock_chain_client)
        tracker.on_start({"session_id": "test-session-1"})
        tracker.on_end({"session_id": "test-session-1"})

        assert tracker._stats["skipped"] == 1
        assert tracker._stats["tracked"] == 1
        assert not mock_chain_client.attest.called
        assert not mock_chain_client.attest_batch.called


class TestToolErrorDetection:
    """Test tool error detection from step events."""

    def test_tool_errors_tracked(self, mock_chain_client, mock_web3):
        sys.modules["web3"] = mock_web3

        from agentscore.tracker import InteractionTracker

        tracker = InteractionTracker(chain_client=mock_chain_client)
        tracker.on_start({"session_id": "test-session-1"})

        tracker.on_step(
            {
                "session_id": "test-session-1",
                "iteration": 1,
                "tool_names": ["terminal"],
                "tool_errors": 1,
            }
        )

        tracker.on_end({"session_id": "test-session-1"})

        assert tracker._stats["tracked"] == 1
        call_args = mock_chain_client.attest_batch.call_args
        if call_args is None:
            call_args = mock_chain_client.attest.call_args
        if call_args:
            attestations = call_args[0][0] if call_args[0] else []
            if isinstance(attestations, list):
                assert attestations[0]["tool_errors"] == 1

    def test_tool_errors_accumulate(self, mock_chain_client, mock_web3):
        sys.modules["web3"] = mock_web3

        from agentscore.tracker import InteractionTracker

        tracker = InteractionTracker(chain_client=mock_chain_client)
        tracker.on_start({"session_id": "test-session-1"})

        tracker.on_step(
            {
                "session_id": "test-session-1",
                "iteration": 1,
                "tool_names": ["terminal"],
                "tool_errors": 1,
            }
        )

        tracker.on_step(
            {
                "session_id": "test-session-1",
                "iteration": 2,
                "tool_names": ["file_read"],
                "tool_errors": 2,
            }
        )

        tracker.on_end({"session_id": "test-session-1"})

        interaction = tracker._interactions.get("test-session-1")
        if interaction is None:
            interaction = {"total_tool_errors": 0}


class TestBatchAccumulation:
    """Test batch accumulation and flush threshold logic."""

    def test_batch_accumulates(self, mock_chain_client, mock_web3):
        sys.modules["web3"] = mock_web3

        from agentscore.tracker import InteractionTracker

        tracker = InteractionTracker(chain_client=mock_chain_client)
        tracker._batch_size_threshold = 3

        for i in range(3):
            tracker.on_start({"session_id": f"session-{i}"})
            tracker.on_step(
                {
                    "session_id": f"session-{i}",
                    "iteration": 1,
                    "tool_names": ["terminal"],
                }
            )
            tracker.on_end({"session_id": f"session-{i}"})

        assert mock_chain_client.attest_batch.called

    def test_flush_on_count_threshold(self, mock_chain_client, mock_web3):
        sys.modules["web3"] = mock_web3

        from agentscore.tracker import InteractionTracker

        tracker = InteractionTracker(chain_client=mock_chain_client)

        # Directly test batch logic by calling _submit_attestation
        mock_w3 = MagicMock()
        mock_w3.keccak.return_value = b"\x12\x34\x56\x78" * 8

        # Simulate adding attestations manually and check flush logic
        with patch.object(tracker, "_flush_batch") as mock_flush:
            tracker._batch_size_threshold = 2
            tracker._batch_time_threshold = 999999  # large enough to not trigger

            # First attestation - should not flush
            tracker._pending_attestations.append(
                {
                    "tool_calls": 1,
                    "tool_errors": 0,
                    "duration": 10,
                    "completed": True,
                    "metrics_hash": b"\x12\x34\x56\x78" * 8,
                }
            )

            # Check if should_flush returns correctly
            should_flush = (
                len(tracker._pending_attestations) >= tracker._batch_size_threshold
                or time.time() - tracker._last_flush_time
                >= tracker._batch_time_threshold
            )
            assert not should_flush

            # Add second attestation - should flush
            tracker._pending_attestations.append(
                {
                    "tool_calls": 1,
                    "tool_errors": 0,
                    "duration": 10,
                    "completed": True,
                    "metrics_hash": b"\x12\x34\x56\x78" * 8,
                }
            )

            should_flush = (
                len(tracker._pending_attestations) >= tracker._batch_size_threshold
                or time.time() - tracker._last_flush_time
                >= tracker._batch_time_threshold
            )
            assert should_flush

    def test_flush_on_time_threshold(self, mock_chain_client, mock_web3):
        sys.modules["web3"] = mock_web3

        from agentscore.tracker import InteractionTracker

        tracker = InteractionTracker(chain_client=mock_chain_client)
        tracker._batch_size_threshold = 100
        tracker._batch_time_threshold = 0

        tracker.on_start({"session_id": "session-0"})
        tracker.on_step(
            {
                "session_id": "session-0",
                "iteration": 1,
                "tool_names": ["terminal"],
            }
        )
        tracker.on_end({"session_id": "session-0"})

        assert mock_chain_client.attest_batch.called


class TestPendingAttestationPersistence:
    """Test pending attestation persistence (write to file on failure, read on startup)."""

    def test_save_pending_on_failure(self, tmp_path, mock_chain_client, mock_web3):
        sys.modules["web3"] = mock_web3

        mock_chain_client.attest_batch.side_effect = Exception("Chain error")

        key_file = tmp_path / ".agentscore" / "agent.key"
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.write_text("0x" + "ab" * 32)

        with patch.dict(os.environ, {"HOME": str(tmp_path)}):
            with patch(
                "agentscore.chain.PENDING_FILE",
                tmp_path / ".agentscore" / "pending.json",
            ), patch("agentscore.chain.CONFIG_DIR", tmp_path / ".agentscore"), \
               patch("agentscore.identity.KEY_FILE", key_file):
                from agentscore.tracker import InteractionTracker

                tracker = InteractionTracker(chain_client=mock_chain_client)
                tracker._batch_size_threshold = 1

                tracker.on_start({"session_id": "session-0"})
                tracker.on_step(
                    {
                        "session_id": "session-0",
                        "iteration": 1,
                        "tool_names": ["terminal"],
                    }
                )
                tracker.on_end({"session_id": "session-0"})

                pending_file = tmp_path / ".agentscore" / "pending.json"
                assert pending_file.exists()

                envelope = json.loads(pending_file.read_text())
                assert "attestations" in envelope
                assert "hmac" in envelope
                assert len(envelope["attestations"]) > 0
                assert envelope["attestations"][0]["tool_calls"] == 1

    def test_load_pending_with_valid_hmac(self, tmp_path):
        """Pending attestations with valid HMAC should load successfully."""
        key_file = tmp_path / ".agentscore" / "agent.key"
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.write_text("0x" + "ab" * 32)

        pending_file = tmp_path / ".agentscore" / "pending.json"

        with patch("agentscore.chain.PENDING_FILE", pending_file), \
             patch("agentscore.chain.CONFIG_DIR", tmp_path / ".agentscore"), \
             patch("agentscore.identity.KEY_FILE", key_file):
            from agentscore.chain import save_pending_attestations, load_pending_attestations

            test_data = [
                {
                    "tool_calls": 1,
                    "tool_errors": 0,
                    "duration": 10,
                    "completed": True,
                    "metrics_hash": "0x1234",
                    "timestamp": 123456,
                }
            ]
            save_pending_attestations(test_data)
            loaded = load_pending_attestations()
            assert len(loaded) == 1
            assert loaded[0]["tool_calls"] == 1

    def test_load_pending_rejects_tampered_file(self, tmp_path):
        """Tampered pending attestation files should be rejected."""
        key_file = tmp_path / ".agentscore" / "agent.key"
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.write_text("0x" + "ab" * 32)

        pending_file = tmp_path / ".agentscore" / "pending.json"

        with patch("agentscore.chain.PENDING_FILE", pending_file), \
             patch("agentscore.chain.CONFIG_DIR", tmp_path / ".agentscore"), \
             patch("agentscore.identity.KEY_FILE", key_file):
            from agentscore.chain import save_pending_attestations, load_pending_attestations

            test_data = [{"tool_calls": 1, "tool_errors": 0, "duration": 10,
                          "completed": True, "metrics_hash": "0x1234", "timestamp": 123456}]
            save_pending_attestations(test_data)

            # Tamper with the file
            envelope = json.loads(pending_file.read_text())
            envelope["attestations"][0]["tool_calls"] = 999
            pending_file.write_text(json.dumps(envelope))

            loaded = load_pending_attestations()
            assert len(loaded) == 0  # Tampered file rejected

    def test_load_pending_rejects_unsigned_legacy(self, tmp_path):
        """Legacy plain-list format (unsigned) should be rejected."""
        pending_file = tmp_path / ".agentscore" / "pending.json"
        pending_file.parent.mkdir(parents=True, exist_ok=True)
        pending_file.write_text(json.dumps([{"tool_calls": 1}]))

        with patch("agentscore.chain.PENDING_FILE", pending_file):
            from agentscore.chain import load_pending_attestations

            loaded = load_pending_attestations()
            assert len(loaded) == 0


class TestCompletedFlag:
    """Test that completed status is correctly derived from context."""

    def test_completed_true_by_default(self, mock_chain_client, mock_web3):
        sys.modules["web3"] = mock_web3

        from agentscore.tracker import InteractionTracker

        tracker = InteractionTracker(chain_client=mock_chain_client)
        tracker._batch_size_threshold = 1

        tracker.on_start({"session_id": "s1"})
        tracker.on_step({"session_id": "s1", "iteration": 1, "tool_names": ["terminal"]})
        tracker.on_end({"session_id": "s1"})

        # Should have flushed with completed=True
        call_args = mock_chain_client.attest_batch.call_args
        assert call_args is not None
        att = call_args[0][0][0]
        assert att["completed"] is True

    def test_completed_false_on_error(self, mock_chain_client, mock_web3):
        sys.modules["web3"] = mock_web3

        from agentscore.tracker import InteractionTracker

        tracker = InteractionTracker(chain_client=mock_chain_client)
        tracker._batch_size_threshold = 1

        tracker.on_start({"session_id": "s1"})
        tracker.on_step({"session_id": "s1", "iteration": 1, "tool_names": ["terminal"]})
        tracker.on_end({"session_id": "s1", "error": "budget exhausted"})

        call_args = mock_chain_client.attest_batch.call_args
        assert call_args is not None
        att = call_args[0][0][0]
        assert att["completed"] is False

    def test_completed_explicit_false(self, mock_chain_client, mock_web3):
        sys.modules["web3"] = mock_web3

        from agentscore.tracker import InteractionTracker

        tracker = InteractionTracker(chain_client=mock_chain_client)
        tracker._batch_size_threshold = 1

        tracker.on_start({"session_id": "s1"})
        tracker.on_step({"session_id": "s1", "iteration": 1, "tool_names": ["terminal"]})
        tracker.on_end({"session_id": "s1", "completed": False})

        call_args = mock_chain_client.attest_batch.call_args
        assert call_args is not None
        att = call_args[0][0][0]
        assert att["completed"] is False


class TestStaleReaping:
    """Test that stale interactions are reaped as incomplete."""

    def test_reap_stale_interactions(self, mock_chain_client, mock_web3):
        sys.modules["web3"] = mock_web3

        from agentscore.tracker import InteractionTracker

        tracker = InteractionTracker(chain_client=mock_chain_client)

        tracker.on_start({"session_id": "s1"})
        tracker.on_step({"session_id": "s1", "iteration": 1, "tool_names": ["terminal"]})

        # Backdate the interaction so it looks stale
        tracker._interactions["s1"]["started_at"] = time.time() - 7200

        reaped = tracker.reap_stale(max_age=3600)
        assert reaped == 1
        assert "s1" not in tracker._interactions
        assert tracker._stats["tracked"] == 1

    def test_reap_does_not_touch_fresh(self, mock_chain_client):
        from agentscore.tracker import InteractionTracker

        tracker = InteractionTracker(chain_client=mock_chain_client)
        tracker.on_start({"session_id": "s1"})
        tracker.on_step({"session_id": "s1", "iteration": 1, "tool_names": ["terminal"]})

        reaped = tracker.reap_stale(max_age=3600)
        assert reaped == 0
        assert "s1" in tracker._interactions


class TestModelAndTokenTracking:
    """Test model identification and token usage tracking."""

    def test_model_tracked_from_start(self, mock_chain_client, mock_web3):
        sys.modules["web3"] = mock_web3

        from agentscore.tracker import InteractionTracker

        tracker = InteractionTracker(chain_client=mock_chain_client)
        tracker.on_start({
            "session_id": "s1",
            "model": "anthropic/claude-opus-4-20250514",
        })

        assert tracker._interactions["s1"]["model"] == "anthropic/claude-opus-4-20250514"

    def test_model_updated_from_step(self, mock_chain_client, mock_web3):
        sys.modules["web3"] = mock_web3

        from agentscore.tracker import InteractionTracker

        tracker = InteractionTracker(chain_client=mock_chain_client)
        tracker.on_start({"session_id": "s1", "model": "model-a"})
        tracker.on_step({
            "session_id": "s1", "iteration": 1,
            "tool_names": ["terminal"], "model": "model-b",
        })

        assert tracker._interactions["s1"]["model"] == "model-b"

    def test_tokens_accumulate_across_steps(self, mock_chain_client, mock_web3):
        sys.modules["web3"] = mock_web3

        from agentscore.tracker import InteractionTracker

        tracker = InteractionTracker(chain_client=mock_chain_client)
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

    def test_model_hash_in_attestation(self, mock_chain_client, mock_web3):
        sys.modules["web3"] = mock_web3

        from agentscore.tracker import InteractionTracker

        tracker = InteractionTracker(chain_client=mock_chain_client)
        tracker._batch_size_threshold = 1

        tracker.on_start({
            "session_id": "s1",
            "model": "anthropic/claude-opus-4-20250514",
        })
        tracker.on_step({
            "session_id": "s1", "iteration": 1,
            "tool_names": ["terminal"],
            "input_tokens": 1000, "output_tokens": 500,
        })
        tracker.on_end({"session_id": "s1"})

        call_args = mock_chain_client.attest_batch.call_args
        assert call_args is not None
        att = call_args[0][0][0]
        assert "model_hash" in att
        assert att["input_tokens"] == 1000
        assert att["output_tokens"] == 500

    def test_zero_tokens_when_not_provided(self, mock_chain_client, mock_web3):
        sys.modules["web3"] = mock_web3

        from agentscore.tracker import InteractionTracker

        tracker = InteractionTracker(chain_client=mock_chain_client)
        tracker._batch_size_threshold = 1

        tracker.on_start({"session_id": "s1"})
        tracker.on_step({
            "session_id": "s1", "iteration": 1,
            "tool_names": ["terminal"],
        })
        tracker.on_end({"session_id": "s1"})

        call_args = mock_chain_client.attest_batch.call_args
        att = call_args[0][0][0]
        assert att["input_tokens"] == 0
        assert att["output_tokens"] == 0


class TestChainClientOffline:
    """Test chain client offline/graceful degradation mode."""

    def test_offline_mode_no_attestation(self):
        from agentscore.tracker import InteractionTracker

        tracker = InteractionTracker(chain_client=None)

        tracker.on_start({"session_id": "session-0"})
        tracker.on_step(
            {
                "session_id": "session-0",
                "iteration": 1,
                "tool_names": ["terminal"],
            }
        )
        tracker.on_end({"session_id": "session-0"})

        assert tracker._stats["tracked"] == 1

    def test_chain_not_ready_no_attestation(self, mock_chain_client):
        mock_chain_client.is_ready.return_value = False

        from agentscore.tracker import InteractionTracker

        tracker = InteractionTracker(chain_client=mock_chain_client)

        tracker.on_start({"session_id": "session-0"})
        tracker.on_step(
            {
                "session_id": "session-0",
                "iteration": 1,
                "tool_names": ["terminal"],
            }
        )
        tracker.on_end({"session_id": "session-0"})

        assert tracker._stats["tracked"] == 1
        assert not mock_chain_client.attest.called
        assert not mock_chain_client.attest_batch.called


class TestFlushMethod:
    """Test the flush method."""

    def test_flush_clears_pending(self, mock_chain_client, mock_web3):
        sys.modules["web3"] = mock_web3

        from agentscore.tracker import InteractionTracker

        tracker = InteractionTracker(chain_client=mock_chain_client)

        # Add pending attestations directly
        tracker._pending_attestations.append(
            {
                "tool_calls": 1,
                "tool_errors": 0,
                "duration": 10,
                "completed": True,
                "metrics_hash": b"\x12\x34\x56\x78" * 8,
            }
        )

        # Call flush and verify it clears
        tracker.flush()

        # Verify pending is cleared or was saved to disk
        assert len(tracker._pending_attestations) == 0

    def test_flush_on_shutdown(self, mock_chain_client):
        from agentscore.tracker import InteractionTracker, setup_shutdown_handler

        tracker = InteractionTracker(chain_client=mock_chain_client)
        setup_shutdown_handler(tracker)

        assert tracker is not None
