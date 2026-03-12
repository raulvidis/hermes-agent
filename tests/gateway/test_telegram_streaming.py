import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from gateway.streaming import StreamLane, StreamingManager, TelegramDraftStream


def test_reasoning_stream_formats_with_real_html_italics():
    bot = AsyncMock()
    bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=101))

    stream = TelegramDraftStream(bot=bot, chat_id=123, lane=StreamLane.REASONING)
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
        assert "should not be sent" not in str(call)
    for call in getattr(bot, 'edit_message_text', AsyncMock()).await_args_list:
        assert "should not be sent" not in str(call)
