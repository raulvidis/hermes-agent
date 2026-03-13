"""
Streaming infrastructure for real-time message updates.

Implements a throttled draft stream loop:
- Uses sendMessageDraft (unofficial Telegram API) in DMs for real-time
  typing effect, falls back to sendMessage/editMessageText in groups
- Coalesced pending updates so we keep the newest state
- Support for both answer and reasoning lanes
"""

import asyncio
import html
import logging
import time
from dataclasses import dataclass
from enum import Enum
from threading import Lock
from typing import Dict, Optional

logger = logging.getLogger(__name__)

_DRAFT_ID_MAX = 2_147_483_647
_next_draft_id = 0


def _allocate_draft_id() -> int:
    global _next_draft_id
    _next_draft_id = _next_draft_id + 1 if _next_draft_id < _DRAFT_ID_MAX else 1
    return _next_draft_id


class StreamLane(Enum):
    """Streaming lanes for different content types."""
    ANSWER = "answer"
    REASONING = "reasoning"


@dataclass
class StreamState:
    """State for an active stream."""
    message_id: Optional[int] = None
    last_sent_text: str = ""
    last_sent_time: float = 0.0


class TelegramDraftStream:
    """Telegram-specific draft stream implementation.

    Uses sendMessageDraft for DM chats (real-time typing effect) with
    automatic fallback to sendMessage/editMessageText for groups or
    when sendMessageDraft is unavailable.
    """

    MAX_CHARS = 4096

    def __init__(
        self,
        bot,
        chat_id: int,
        lane: StreamLane,
        thread_id: Optional[int] = None,
        throttle_ms: int = 250,
        parse_mode: str = "HTML",
        initial_min_chars: int = 30,
        use_draft_transport: bool = False,
    ):
        self._bot = bot
        self._chat_id = chat_id
        self._lane = lane
        self._thread_id = thread_id
        self._throttle_ms = throttle_ms
        self._parse_mode = parse_mode
        self._initial_min_chars = initial_min_chars

        self._state = StreamState()
        self._lock = Lock()
        self._flush_lock = asyncio.Lock()
        self._pending = ""
        self._stopped = False

        # Draft transport state
        self._use_draft = use_draft_transport
        self._draft_id: Optional[int] = _allocate_draft_id() if use_draft_transport else None
        self._draft_failed = False  # Fallback flag if sendMessageDraft is rejected

    @property
    def message_id(self) -> Optional[int]:
        return self._state.message_id

    @property
    def last_sent_text(self) -> str:
        return self._state.last_sent_text

    @property
    def using_draft_transport(self) -> bool:
        return self._use_draft and not self._draft_failed

    def update(self, text: str) -> None:
        """Coalesce pending text to the most recent version."""
        with self._lock:
            self._pending = text or ""

    def stop(self) -> None:
        self._stopped = True

    async def flush(self, *, allow_stopped: bool = False) -> None:
        await self._send_update(allow_stopped=allow_stopped)

    async def clear(self) -> None:
        with self._lock:
            self._pending = ""
            self._state = StreamState()

    def _format_text(self, text: str) -> str:
        text = text or ""
        # Escape first so truncation respects the expanded length
        escaped = html.escape(text, quote=False)
        # All streaming previews are italic (they're temporary and get deleted)
        max_len = self.MAX_CHARS - 7  # Reserve room for <i></i>
        if len(escaped) > max_len:
            escaped = escaped[: max_len - 3] + "..."
        return f"<i>{escaped}</i>"

    async def _send_draft(self, formatted: str) -> bool:
        """Try sendMessageDraft via do_api_request. Returns True on success."""
        if not self._use_draft or self._draft_failed:
            return False

        try:
            api_kwargs = {
                "chat_id": self._chat_id,
                "draft_id": self._draft_id,
                "text": formatted,
            }
            if self._parse_mode:
                api_kwargs["parse_mode"] = self._parse_mode
            if self._thread_id is not None:
                api_kwargs["message_thread_id"] = self._thread_id

            await self._bot.do_api_request(
                "sendMessageDraft",
                api_kwargs=api_kwargs,
            )
            return True
        except Exception as e:
            err_msg = str(e).lower()
            # Permanent failures — fall back to message transport
            if any(kw in err_msg for kw in (
                "unknown method", "not found", "not available",
                "not supported", "unsupported", "can't be used",
                "can be used only",
            )):
                logger.warning("sendMessageDraft unavailable, falling back to editMessageText: %s", e)
                self._draft_failed = True
                return False
            # Transient error — still try fallback this time
            logger.debug("sendMessageDraft error (will retry): %s", e)
            return False

    async def _send_update(self, *, allow_stopped: bool = False) -> Optional[Dict[str, int]]:
        async with self._flush_lock:
            if self._stopped and not allow_stopped:
                return None

            with self._lock:
                text = self._pending

            if not text:
                return None
            if (
                self._state.message_id is None
                and len(text) < self._initial_min_chars
                and not allow_stopped
            ):
                return None
            if text == self._state.last_sent_text:
                return None

            formatted = self._format_text(text)
            logger.debug("stream _send_update: len=%d msg_id=%s draft=%s", len(text), self._state.message_id, self.using_draft_transport)

            # Try draft transport first (typing bubble effect in DMs)
            if self.using_draft_transport:
                if await self._send_draft(formatted):
                    self._state.last_sent_text = text
                    self._state.last_sent_time = time.time()
                    return None  # No message_id for drafts

            # Fallback: sendMessage / editMessageText
            try:
                if self._state.message_id is not None:
                    result = await self._bot.edit_message_text(
                        chat_id=self._chat_id,
                        message_id=self._state.message_id,
                        text=formatted,
                        parse_mode=self._parse_mode,
                    )
                else:
                    kwargs = {
                        "chat_id": self._chat_id,
                        "text": formatted,
                        "parse_mode": self._parse_mode,
                    }
                    if self._thread_id is not None:
                        kwargs["message_thread_id"] = self._thread_id
                    result = await self._bot.send_message(**kwargs)

                if result:
                    self._state.message_id = result.message_id
                    self._state.last_sent_text = text
                    self._state.last_sent_time = time.time()
                    return {"message_id": result.message_id}
            except Exception as e:
                logger.warning("Telegram draft stream error: %s", e)

            return None


class StreamingManager:
    """Manages multiple concurrent streams per chat."""

    def __init__(self, adapter, throttle_ms: int = 250):
        self._adapter = adapter
        self._throttle_ms = throttle_ms
        self._streams: Dict[str, TelegramDraftStream] = {}
        self._tasks: Dict[str, asyncio.Task] = {}

    def get_stream_key(self, chat_id: int, lane: StreamLane) -> str:
        return f"{chat_id}:{lane.value}"

    async def start_stream(
        self,
        chat_id: int,
        thread_id: Optional[int] = None,
        lane: StreamLane = StreamLane.ANSWER,
        initial_min_chars: int = 30,
    ) -> str:
        key = self.get_stream_key(chat_id, lane)
        await self.stop_stream(chat_id, lane)

        stream = TelegramDraftStream(
            bot=self._adapter.bot,
            chat_id=chat_id,
            lane=lane,
            thread_id=thread_id,
            throttle_ms=self._throttle_ms,
            initial_min_chars=initial_min_chars,
        )
        self._streams[key] = stream

        async def run_stream():
            try:
                while not stream._stopped:
                    await stream.flush()
                    await asyncio.sleep(self._throttle_ms / 1000.0)
            except asyncio.CancelledError:
                raise

        self._tasks[key] = asyncio.create_task(run_stream())
        return key

    async def update_stream(
        self,
        chat_id: int,
        text: str,
        lane: StreamLane = StreamLane.ANSWER,
    ) -> None:
        stream = self._streams.get(self.get_stream_key(chat_id, lane))
        if stream:
            stream.update(text)

    async def flush_stream(
        self,
        chat_id: int,
        lane: StreamLane = StreamLane.ANSWER,
    ) -> None:
        stream = self._streams.get(self.get_stream_key(chat_id, lane))
        if stream:
            await stream.flush()

    async def stop_stream(
        self,
        chat_id: int,
        lane: StreamLane = StreamLane.ANSWER,
    ) -> None:
        key = self.get_stream_key(chat_id, lane)
        stream = self._streams.pop(key, None)
        task = self._tasks.pop(key, None)

        if stream:
            stream.stop()
            await stream.flush(allow_stopped=True)

        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def discard_stream(
        self,
        chat_id: int,
        lane: StreamLane = StreamLane.ANSWER,
    ) -> Optional[int]:
        """Stop a stream WITHOUT flushing and return its message_id for deletion."""
        key = self.get_stream_key(chat_id, lane)
        stream = self._streams.pop(key, None)
        task = self._tasks.pop(key, None)

        message_id = stream.message_id if stream else None

        if stream:
            stream.stop()
            # Do NOT flush — caller will delete the message

        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        return message_id

    async def stop_all_streams(self, chat_id: int) -> None:
        for lane in StreamLane:
            await self.stop_stream(chat_id, lane)

    def get_stream_message_id(
        self,
        chat_id: int,
        lane: StreamLane = StreamLane.ANSWER,
    ) -> Optional[int]:
        stream = self._streams.get(self.get_stream_key(chat_id, lane))
        return stream.message_id if stream else None
