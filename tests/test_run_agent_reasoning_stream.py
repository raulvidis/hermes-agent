"""Tests for reasoning streaming callbacks in AIAgent."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from run_agent import AIAgent


def _make_agent():
    tool_defs = [
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "web_search tool",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    with (
        patch("run_agent.get_tool_definitions", return_value=tool_defs),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        agent = AIAgent(
            api_key="test-key",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
        )
        agent.client = MagicMock()
        return agent


def test_streaming_chat_completion_emits_reasoning_callback():
    agent = _make_agent()
    seen = []
    agent.reasoning_callback = seen.append

    delta_1 = SimpleNamespace(content=None, tool_calls=None, reasoning="step 1")
    delta_2 = SimpleNamespace(content="done", tool_calls=None, reasoning="step 1\nstep 2")
    chunk_1 = SimpleNamespace(choices=[SimpleNamespace(delta=delta_1)], usage=None)
    chunk_2 = SimpleNamespace(choices=[SimpleNamespace(delta=delta_2)], usage=None)
    chunk_3 = SimpleNamespace(choices=[], usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2))
    agent.client.chat.completions.create.return_value = [chunk_1, chunk_2, chunk_3]

    response = agent._run_streaming_chat_completion({"model": "test", "messages": []})

    assert seen == ["step 1", "step 1\nstep 2"]
    assert response.choices[0].message.content == "done"
