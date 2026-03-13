"""Tests for topic-aware gateway progress updates."""

import asyncio
import importlib
import sys
import time
import types
from types import SimpleNamespace

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import BasePlatformAdapter, SendResult
from gateway.session import SessionSource


class ProgressCaptureAdapter(BasePlatformAdapter):
    def __init__(self):
        super().__init__(PlatformConfig(enabled=True, token="fake-token"), Platform.TELEGRAM)
        self.sent = []
        self.edits = []
        self.typing = []

    async def connect(self) -> bool:
        return True

    async def disconnect(self) -> None:
        return None

    async def send(self, chat_id, content, reply_to=None, metadata=None) -> SendResult:
        self.sent.append(
            {
                "chat_id": chat_id,
                "content": content,
                "reply_to": reply_to,
                "metadata": metadata,
            }
        )
        return SendResult(success=True, message_id="progress-1")

    async def edit_message(self, chat_id, message_id, content) -> SendResult:
        self.edits.append(
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "content": content,
            }
        )
        return SendResult(success=True, message_id=message_id)

    async def send_typing(self, chat_id, metadata=None) -> None:
        self.typing.append({"chat_id": chat_id, "metadata": metadata})

    async def get_chat_info(self, chat_id: str):
        return {"id": chat_id}


class StreamingCaptureAdapter(ProgressCaptureAdapter):
    def __init__(self):
        super().__init__()
        self.streaming_enabled = True
        self.stream_starts = []
        self.stream_updates = []
        self.stream_ends = []

    async def stream_start(self, chat_id, initial_text="", thread_id=None, lane=None):
        self.stream_starts.append({
            "chat_id": chat_id,
            "initial_text": initial_text,
            "thread_id": thread_id,
            "lane": lane,
        })
        return SendResult(success=True, raw_response={"stream_key": f"{lane.value}-1"})

    async def stream_update(self, chat_id, text, lane=None):
        self.stream_updates.append({
            "chat_id": chat_id,
            "text": text,
            "lane": lane,
        })
        return SendResult(success=True)

    async def stream_end(self, chat_id, final_text=None, lane=None):
        self.stream_ends.append({
            "chat_id": chat_id,
            "final_text": final_text,
            "lane": lane,
        })
        return SendResult(success=True)



class FakeAgent:
    def __init__(self, **kwargs):
        self.tool_progress_callback = kwargs["tool_progress_callback"]
        self.tools = []

    def run_conversation(self, message, conversation_history=None, task_id=None):
        self.tool_progress_callback("terminal", "pwd")
        time.sleep(0.35)
        self.tool_progress_callback("browser_navigate", "https://example.com")
        time.sleep(0.35)
        return {
            "final_response": "done",
            "messages": [],
            "api_calls": 1,
        }


class FakeStreamingAgent:
    def __init__(self, **kwargs):
        self.tool_progress_callback = kwargs["tool_progress_callback"]
        self.streaming_callback = kwargs["streaming_callback"]
        self.reasoning_callback = kwargs["reasoning_callback"]
        self.tools = []

    def run_conversation(self, message, conversation_history=None, task_id=None):
        self.tool_progress_callback("terminal", "pwd")
        self.reasoning_callback("thinking")
        self.streaming_callback("partial answer")
        time.sleep(0.35)
        self.tool_progress_callback("browser_navigate", "https://example.com")
        self.streaming_callback("partial answer complete")
        time.sleep(0.35)
        return {
            "final_response": "done",
            "messages": [],
            "api_calls": 1,
        }


def _make_runner(adapter):
    gateway_run = importlib.import_module("gateway.run")
    GatewayRunner = gateway_run.GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner._prefill_messages = []
    runner._ephemeral_system_prompt = ""
    runner._reasoning_config = None
    runner._provider_routing = {}
    runner._fallback_model = None
    runner._session_db = None
    runner._running_agents = {}
    runner.hooks = SimpleNamespace(loaded_hooks=False)
    return runner


@pytest.mark.asyncio
async def test_run_agent_progress_stays_in_originating_topic(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_TOOL_PROGRESS_MODE", "all")

    fake_dotenv = types.ModuleType("dotenv")
    fake_dotenv.load_dotenv = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "dotenv", fake_dotenv)

    fake_run_agent = types.ModuleType("run_agent")
    fake_run_agent.AIAgent = FakeAgent
    monkeypatch.setitem(sys.modules, "run_agent", fake_run_agent)

    adapter = ProgressCaptureAdapter()
    runner = _make_runner(adapter)
    gateway_run = importlib.import_module("gateway.run")
    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    monkeypatch.setattr(gateway_run, "_resolve_runtime_agent_kwargs", lambda: {"api_key": "fake"})
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="-1001",
        chat_type="group",
        thread_id="17585",
    )

    result = await runner._run_agent(
        message="hello",
        context_prompt="",
        history=[],
        source=source,
        session_id="sess-1",
        session_key="agent:main:telegram:group:-1001:17585",
    )

    assert result["final_response"] == "done"
    assert adapter.sent == [
        {
            "chat_id": "-1001",
            "content": '💻 terminal: "pwd"',
            "reply_to": None,
            "metadata": {"thread_id": "17585"},
        }
    ]
    assert adapter.edits
    assert all(call["metadata"] == {"thread_id": "17585"} for call in adapter.typing)


def test_telegram_streaming_merges_progress_and_answer(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_TOOL_PROGRESS_MODE", "all")

    fake_dotenv = types.ModuleType("dotenv")
    fake_dotenv.load_dotenv = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "dotenv", fake_dotenv)

    fake_run_agent = types.ModuleType("run_agent")
    fake_run_agent.AIAgent = FakeStreamingAgent
    monkeypatch.setitem(sys.modules, "run_agent", fake_run_agent)

    adapter = StreamingCaptureAdapter()
    runner = _make_runner(adapter)
    gateway_run = importlib.import_module("gateway.run")
    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    monkeypatch.setattr(gateway_run, "_resolve_runtime_agent_kwargs", lambda: {"api_key": "fake"})
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="-1001",
        chat_type="group",
        thread_id="17585",
    )

    result = asyncio.run(runner._run_agent(
        message="hello",
        context_prompt="",
        history=[],
        source=source,
        session_id="sess-2",
        session_key="agent:main:telegram:group:-1001:17585",
    ))

    assert result["final_response"] == "done"
    assert adapter.sent == []
    assert adapter.stream_starts
    answer_payloads = [item["initial_text"] for item in adapter.stream_starts if item["lane"].value == "answer"]
    answer_payloads += [item["text"] for item in adapter.stream_updates if item["lane"].value == "answer"]
    assert any('💻 terminal: "pwd"' in payload for payload in answer_payloads)
    assert any('🌐 browser_navigate: "https://example.com"' in payload for payload in answer_payloads)
    assert any('partial answer complete' in payload for payload in answer_payloads)
    assert any("thinking" in payload for payload in answer_payloads)
