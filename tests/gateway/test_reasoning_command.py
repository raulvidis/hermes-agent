"""Tests for gateway /reasoning command behavior."""

from unittest.mock import MagicMock

import pytest

from gateway.config import Platform
from gateway.platforms.base import MessageEvent
from gateway.session import SessionSource


def _make_event(text="/reasoning", platform=Platform.TELEGRAM, user_id="123", chat_id="456"):
    source = SessionSource(
        platform=platform,
        user_id=user_id,
        chat_id=chat_id,
        user_name="tester",
    )
    return MessageEvent(text=text, source=source)


def _make_runner():
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner.adapters = {}
    runner._running_agents = {}
    session_entry = MagicMock()
    session_entry.session_id = "session_123"
    session_entry.session_key = "agent:main:telegram:dm"
    session_entry.reasoning_mode = "off"

    store = MagicMock()
    store.get_or_create_session.return_value = session_entry
    runner.session_store = store
    return runner, store, session_entry


@pytest.mark.asyncio
async def test_reasoning_command_shows_current_mode():
    runner, _, _ = _make_runner()

    result = await runner._handle_reasoning_command(_make_event("/reasoning"))

    assert "Current reasoning mode" in result
    assert "/reasoning off" in result


@pytest.mark.asyncio
async def test_reasoning_command_persists_stream_mode():
    runner, store, session_entry = _make_runner()

    result = await runner._handle_reasoning_command(_make_event("/reasoning stream"))

    store.set_reasoning_mode.assert_called_once_with(session_entry.session_key, "stream")
    assert "stream live" in result

