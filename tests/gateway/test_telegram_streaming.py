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
