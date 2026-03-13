import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from gateway.streaming import StreamLane, StreamingManager, TelegramDraftStream


def test_reasoning_stream_formats_with_real_html_italics():
    bot = AsyncMock()
    bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=101))

    stream = TelegramDraftStream(
        bot=bot, chat_id=123, lane=StreamLane.REASONING, use_draft_transport=False,
    )
    stream.update("think <fast> & clearly" * 3)
    asyncio.run(stream.flush())

    bot.send_message.assert_awaited_once()
    sent_text = bot.send_message.await_args.kwargs["text"]
    assert sent_text.startswith("<i>")
    assert sent_text.endswith("</i>")
    assert "&lt;fast&gt;" in sent_text
    assert "&amp;" in sent_text


def test_stop_stream_flushes_latest_pending_update():
    bot = AsyncMock()
    bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=202))
    adapter = SimpleNamespace(bot=bot)
    manager = StreamingManager(adapter, throttle_ms=10)

    async def _run():
        await manager.start_stream(chat_id=77, lane=StreamLane.ANSWER)
        # Disable draft transport on the stream so it uses sendMessage
        key = manager.get_stream_key(77, StreamLane.ANSWER)
        manager._streams[key]._use_draft = False
        await manager.update_stream(chat_id=77, text="final pending update", lane=StreamLane.ANSWER)
        await manager.stop_stream(chat_id=77, lane=StreamLane.ANSWER)

    asyncio.run(_run())

    bot.send_message.assert_awaited_once()
    sent_text = bot.send_message.await_args.kwargs["text"]
    assert sent_text == "<i>final pending update</i>"


def test_discard_stream_returns_message_id_without_flushing():
    """discard_stream should return the message_id and NOT send pending text."""
    bot = AsyncMock()
    bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=303))
    adapter = SimpleNamespace(bot=bot)
    manager = StreamingManager(adapter, throttle_ms=10)

    async def _run():
        await manager.start_stream(chat_id=88, lane=StreamLane.ANSWER)
        key = manager.get_stream_key(88, StreamLane.ANSWER)
        manager._streams[key]._use_draft = False
        await manager.update_stream(chat_id=88, text="a]" * 20, lane=StreamLane.ANSWER)
        # Let the background loop send the first message
        await asyncio.sleep(0.05)
        # Now discard — should return the message_id without flushing new text
        await manager.update_stream(chat_id=88, text="should not be sent", lane=StreamLane.ANSWER)
        mid = await manager.discard_stream(chat_id=88, lane=StreamLane.ANSWER)
        return mid

    mid = asyncio.run(_run())
    assert mid == 303
    # The "should not be sent" text should NOT have been flushed
    for call in bot.send_message.await_args_list:
        text = call.kwargs.get("text", "")
        assert "should not be sent" not in text
    for call in getattr(bot, 'edit_message_text', AsyncMock()).await_args_list:
        text = call.kwargs.get("text", "")
        assert "should not be sent" not in text


def test_stream_start_materializes_initial_preview_for_cleanup():
    """stream_start should flush initial_text so cleanup can recover message_id."""
    from gateway.config import PlatformConfig
    from gateway.platforms.telegram import TelegramAdapter

    bot = AsyncMock()
    bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=404))

    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="fake", streaming_enabled=True))
    adapter._bot = bot
    adapter._streaming_manager = StreamingManager(adapter, throttle_ms=10)

    async def _run():
        result = await adapter.stream_start(
            chat_id="123",
            initial_text='Running:\n💻 terminal: "pwd"',
            lane=StreamLane.ANSWER,
        )
        delete_result = await adapter.stream_delete(
            chat_id="123",
            lane=StreamLane.ANSWER,
            deferred=True,
        )
        return result, delete_result

    result, delete_result = asyncio.run(_run())

    assert result.success is True
    bot.send_message.assert_awaited_once()
    assert delete_result.success is True
    assert delete_result.message_id == "404"


def test_concurrent_flushes_do_not_create_duplicate_preview_messages():
    bot = AsyncMock()
    bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=505))

    stream = TelegramDraftStream(
        bot=bot, chat_id=123, lane=StreamLane.ANSWER, use_draft_transport=False,
    )
    stream.update("hello")

    async def _run():
        await asyncio.gather(stream.flush(), stream.flush())

    asyncio.run(_run())

    bot.send_message.assert_awaited_once()
    assert stream.message_id == 505


def test_draft_transport_calls_do_api_request():
    """Draft transport should call do_api_request with sendMessageDraft."""
    bot = AsyncMock()
    bot.do_api_request = AsyncMock(return_value=True)

    stream = TelegramDraftStream(
        bot=bot, chat_id=123, lane=StreamLane.ANSWER, use_draft_transport=True,
    )
    stream.update("hello world — streaming draft test")
    asyncio.run(stream.flush())

    bot.do_api_request.assert_awaited_once()
    call_args = bot.do_api_request.await_args
    assert call_args.args[0] == "sendMessageDraft"
    api_kwargs = call_args.kwargs.get("api_kwargs") or call_args.args[1]
    assert api_kwargs["chat_id"] == 123
    assert api_kwargs["text"].startswith("<i>")
    assert "draft_id" in api_kwargs
    # send_message should NOT have been called
    bot.send_message.assert_not_awaited()


def test_draft_transport_falls_back_on_unsupported():
    """When sendMessageDraft fails with 'unknown method', fall back to sendMessage."""
    bot = AsyncMock()
    bot.do_api_request = AsyncMock(
        side_effect=Exception("400: unknown method sendMessageDraft"),
    )
    bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=501))

    stream = TelegramDraftStream(
        bot=bot, chat_id=456, lane=StreamLane.ANSWER, use_draft_transport=True,
    )
    stream.update("fallback test with enough characters")
    asyncio.run(stream.flush())

    # Draft was attempted
    bot.do_api_request.assert_awaited_once()
    # Fell back to sendMessage
    bot.send_message.assert_awaited_once()
    assert stream.message_id == 501
    # Draft transport should now be disabled
    assert not stream.using_draft_transport
