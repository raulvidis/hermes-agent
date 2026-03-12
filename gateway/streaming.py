"""
Streaming infrastructure for real-time message updates.

Implements a throttled draft stream loop similar to OpenClaw's approach:
- Throttled updates (default 1000ms between edits)
- Debounced sending to avoid API rate limits
- Support for both answer and reasoning lanes
"""

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional, Dict, Any
from threading import Lock

logger = logging.getLogger(__name__)


class StreamLane(Enum):
    """Streaming lanes for different content types."""
    ANSWER = "answer"
    REASONING = "reasoning"


@dataclass
class StreamState:
    """State for an active stream."""
    message_id: Optional[int] = None
    text: str = ""
    last_sent_text: str = ""
    last_sent_time: float = 0.0
    is_active: bool = True
    force_new_message: bool = False
    

class DraftStreamLoop:
    """
    Throttled loop for streaming message updates.
    
    Similar to OpenClaw's draft-stream-loop.ts:
    - Accumulates text via update()
    - Sends debounced updates at throttle interval
    - Tracks in-flight messages for sequential delivery
    """
    
    def __init__(
        self,
        send_fn: Callable[[str, Optional[int]], Any],
        throttle_ms: int = 1000,
        max_chars: int = 4096,
        initial_min_chars: int = 30,
    ):
        """
        Args:
            send_fn: Async function to send/edit message. Takes (text, message_id).
            throttle_ms: Minimum time between sends (default 1000ms for Telegram).
            max_chars: Maximum characters per message (4096 for Telegram).
            initial_min_chars: Minimum chars before first send (for push notification quality).
        """
        self._send_fn = send_fn
        self._throttle_ms = throttle_ms
        self._max_chars = max_chars
        self._initial_min_chars = initial_min_chars
        
        self._state = StreamState()
        self._lock = Lock()
        self._pending = ""
        self._stopped = False
        self._loop_task: Optional[asyncio.Task] = None
        self._in_flight = False
        
    def update(self, text: str) -> None:
        """Push new text to the stream."""
        with self._lock:
            self._pending = text
            
    def force_new_message(self) -> None:
        """Force creation of a new message on next send."""
        with self._lock:
            self._state.force_new_message = True
            self._state.message_id = None
            
    def stop(self) -> None:
        """Stop the stream loop."""
        self._stopped = True
        
    def reset_pending(self) -> None:
        """Reset the pending buffer."""
        with self._lock:
            self._pending = ""
            
    @property
    def message_id(self) -> Optional[int]:
        """Get the current message ID."""
        return self._state.message_id
        
    @property
    def last_sent_text(self) -> str:
        """Get the last sent text."""
        return self._state.last_sent_text
        
    async def flush(self) -> None:
        """Flush pending text immediately (even if stopped)."""
        await self._send_pending(force_flush=True)
        
    async def _send_pending(self, force_flush: bool = False) -> None:
        """Send pending text if there are changes.

        Args:
            force_flush: If True, ignore the _stopped flag (used for final flush).
        """
        if self._stopped and not force_flush:
            return

        with self._lock:
            text = self._pending
            force_new = self._state.force_new_message
            self._state.force_new_message = False
            msg_id_snapshot = self._state.message_id
            last_sent_snapshot = self._state.last_sent_text

        if not text:
            return

        # Check if we have enough text for initial send
        if msg_id_snapshot is None and len(text) < self._initial_min_chars:
            return

        # Check if text changed since last send
        if text == last_sent_snapshot:
            return

        # Truncate if needed
        send_text = text[:self._max_chars]

        # Determine message ID to use
        msg_id = None if force_new else msg_id_snapshot

        try:
            result = await self._send_fn(send_text, msg_id)
            with self._lock:
                if result and "message_id" in result:
                    self._state.message_id = result["message_id"]
                self._state.last_sent_text = send_text
                self._state.last_sent_time = time.time()
        except Exception as e:
            logger.warning(f"Draft stream send failed: {e}")
            
    async def run(self) -> None:
        """Main loop - call this to start the stream."""
        while not self._stopped:
            await self._send_pending()
            await asyncio.sleep(self._throttle_ms / 1000.0)


class TelegramDraftStream:
    """
    Telegram-specific draft stream implementation.
    
    Handles:
    - Message creation and editing
    - HTML formatting for Telegram
    - Character limits (4096)
    - Thread/topic support
    """
    
    MAX_CHARS = 4096
    
    def __init__(
        self,
        bot,
        chat_id: int,
        thread_id: Optional[int] = None,
        throttle_ms: int = 1000,
        parse_mode: str = "HTML",
    ):
        self._bot = bot
        self._chat_id = chat_id
        self._thread_id = thread_id
        self._parse_mode = parse_mode
        
        self._state = StreamState()
        self._lock = Lock()
        self._pending = ""
        self._stopped = False
        
    @property
    def message_id(self) -> Optional[int]:
        return self._state.message_id
        
    @property
    def last_sent_text(self) -> str:
        return self._state.last_sent_text
        
    def update(self, text: str) -> None:
        """Update the draft text."""
        with self._lock:
            self._pending = text
            
    def force_new_message(self) -> None:
        """Force a new message on next send."""
        with self._lock:
            self._state.force_new_message = True
            self._state.message_id = None
            
    def stop(self) -> None:
        """Stop streaming."""
        self._stopped = True
        
    async def flush(self) -> None:
        """Immediately send pending text (even if stopped)."""
        await self._send_update(force_flush=True)
        
    async def clear(self) -> None:
        """Clear the stream state."""
        with self._lock:
            self._pending = ""
            self._state = StreamState()
            
    def _format_text(self, text: str, is_reasoning: bool = False) -> str:
        """Format text for Telegram."""
        # Escape HTML entities first (before truncation, since escaping expands chars)
        text = text.replace("&", "&amp;")
        text = text.replace("<", "&lt;")
        text = text.replace(">", "&gt;")

        # Reserve room for wrapper tags
        max_len = self.MAX_CHARS - (7 if is_reasoning else 0)  # len("<i></i>") == 7

        # Truncate after escaping so we respect the real byte budget
        if len(text) > max_len:
            text = text[:max_len - 3] + "..."

        # Format reasoning differently (italic)
        if is_reasoning:
            text = f"<i>{text}</i>"

        return text
        
    async def _send_update(self, force_flush: bool = False) -> Optional[Dict[str, Any]]:
        """Send or edit the message.

        Args:
            force_flush: If True, ignore the _stopped flag (used for final flush).
        """
        if self._stopped and not force_flush:
            return None

        with self._lock:
            text = self._pending
            force_new = self._state.force_new_message
            self._state.force_new_message = False
            msg_id_snapshot = self._state.message_id
            last_sent_snapshot = self._state.last_sent_text

        if not text:
            return None

        # Skip if unchanged
        if text == last_sent_snapshot:
            return None

        # Minimum chars for first message (push notification quality)
        if msg_id_snapshot is None and len(text) < 30:
            return None

        formatted = self._format_text(text)

        try:
            if msg_id_snapshot is not None and not force_new:
                # Edit existing message
                result = await self._bot.edit_message_text(
                    chat_id=self._chat_id,
                    message_id=msg_id_snapshot,
                    text=formatted,
                    parse_mode=self._parse_mode,
                )
            else:
                # Send new message
                kwargs = {
                    "chat_id": self._chat_id,
                    "text": formatted,
                    "parse_mode": self._parse_mode,
                }
                if self._thread_id is not None:
                    kwargs["message_thread_id"] = self._thread_id

                result = await self._bot.send_message(**kwargs)

            if result:
                with self._lock:
                    self._state.message_id = result.message_id
                    self._state.last_sent_text = text
                    self._state.last_sent_time = time.time()
                return {"message_id": result.message_id}

        except Exception as e:
            logger.warning(f"Telegram draft stream error: {e}")

        return None


class ReasoningLaneCoordinator:
    """
    Coordinates dual-lane streaming (answer + reasoning).
    
    Splits text into reasoning and answer portions based on:
    - <think/>, <thinking/>, <thought/> tags
    - "Reasoning:" prefix patterns
    """
    
    THINKING_TAG_RE = re.compile(
        r'<\s*(/?)\s*(?:think(?:ing)?|thought|antthinking)\b[^<>]*>',
        re.IGNORECASE,
    )

    @classmethod
    def split_text(cls, text: str) -> Dict[str, str]:
        """
        Split text into reasoning and answer portions.

        Returns:
            {"reasoning": str, "answer": str}
        """
        if not text:
            return {"reasoning": "", "answer": ""}

        matches = list(cls.THINKING_TAG_RE.finditer(text))
        if not matches:
            # Check for "Reasoning:" prefix pattern (handled below)
            pass
        else:
            reasoning_parts: list[str] = []
            answer_parts: list[str] = []
            in_thinking = False
            last_pos = 0

            for match in matches:
                tag_start, tag_end = match.span()
                is_closing = bool(match.group(1))  # group(1) captures the optional "/"

                segment = text[last_pos:tag_start]

                if not in_thinking and not is_closing:
                    # Opening tag — preceding segment is answer text
                    if segment:
                        answer_parts.append(segment)
                    in_thinking = True
                elif in_thinking and is_closing:
                    # Closing tag — preceding segment is reasoning text
                    if segment:
                        reasoning_parts.append(segment)
                    in_thinking = False
                else:
                    # Mismatched tag (e.g. double-open or double-close) — keep segment
                    if segment:
                        (reasoning_parts if in_thinking else answer_parts).append(segment)

                last_pos = tag_end

            # Trailing text after the last tag
            trailing = text[last_pos:]
            if trailing:
                (reasoning_parts if in_thinking else answer_parts).append(trailing)

            return {
                "reasoning": "".join(reasoning_parts),
                "answer": "".join(answer_parts),
            }
            
        # Check for "Reasoning:" prefix pattern
        if text.strip().startswith("Reasoning:"):
            lines = text.split("\n", 1)
            if len(lines) > 1:
                return {
                    "reasoning": lines[0].replace("Reasoning:", "").strip(),
                    "answer": lines[1].strip()
                }
            return {"reasoning": lines[0].replace("Reasoning:", "").strip(), "answer": ""}
            
        # No reasoning detected - all answer
        return {"reasoning": "", "answer": text}


class StreamingManager:
    """
    Manages multiple concurrent streams per chat.
    
    Provides a high-level interface for:
    - Starting/stopping streams
    - Routing text to appropriate lanes
    - Coordinating answer and reasoning streams
    """
    
    def __init__(self, adapter, throttle_ms: int = 1000):
        """
        Args:
            adapter: Platform adapter (e.g., TelegramAdapter)
            throttle_ms: Throttle interval in milliseconds
        """
        self._adapter = adapter
        self._throttle_ms = throttle_ms
        self._streams: Dict[str, TelegramDraftStream] = {}
        self._tasks: Dict[str, asyncio.Task] = {}
        
    def get_stream_key(self, chat_id: int, lane: StreamLane) -> str:
        """Generate a unique key for a stream."""
        return f"{chat_id}:{lane.value}"
        
    async def start_stream(
        self,
        chat_id: int,
        thread_id: Optional[int] = None,
        lane: StreamLane = StreamLane.ANSWER,
    ) -> str:
        """
        Start a new stream for a chat.
        
        Returns:
            Stream key for subsequent updates
        """
        key = self.get_stream_key(chat_id, lane)
        
        # Stop existing stream if any
        await self.stop_stream(chat_id, lane)
        
        # Create new stream
        stream = TelegramDraftStream(
            bot=self._adapter.bot,
            chat_id=chat_id,
            thread_id=thread_id,
            throttle_ms=self._throttle_ms,
        )
        self._streams[key] = stream
        
        # Start the stream loop
        async def run_stream():
            while not stream._stopped:
                await stream._send_update()
                await asyncio.sleep(self._throttle_ms / 1000.0)
                
        self._tasks[key] = asyncio.create_task(run_stream())
        
        return key
        
    async def update_stream(
        self,
        chat_id: int,
        text: str,
        lane: StreamLane = StreamLane.ANSWER,
    ) -> None:
        """Update text in a stream."""
        key = self.get_stream_key(chat_id, lane)
        stream = self._streams.get(key)
        if stream:
            stream.update(text)
            
    async def flush_stream(
        self,
        chat_id: int,
        lane: StreamLane = StreamLane.ANSWER,
    ) -> None:
        """Flush a stream immediately."""
        key = self.get_stream_key(chat_id, lane)
        stream = self._streams.get(key)
        if stream:
            await stream.flush()
            
    async def stop_stream(
        self,
        chat_id: int,
        lane: StreamLane = StreamLane.ANSWER,
    ) -> None:
        """Stop and clean up a stream."""
        key = self.get_stream_key(chat_id, lane)
        
        stream = self._streams.pop(key, None)
        if stream:
            await stream.flush()  # Flush before stopping so final text is sent
            stream.stop()
            
        task = self._tasks.pop(key, None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
                
    async def stop_all_streams(self, chat_id: int) -> None:
        """Stop all streams for a chat."""
        for lane in StreamLane:
            await self.stop_stream(chat_id, lane)
            
    def get_stream_message_id(
        self,
        chat_id: int,
        lane: StreamLane = StreamLane.ANSWER,
    ) -> Optional[int]:
        """Get the message ID for a stream."""
        key = self.get_stream_key(chat_id, lane)
        stream = self._streams.get(key)
        return stream.message_id if stream else None
