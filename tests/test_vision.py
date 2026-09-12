from unittest.mock import MagicMock, patch
import pytest

from deskpilot.agent_tasks.screenshot_agent.vision import (
    get_ollama_reasoning_llm,
    get_ollama_vision_llm,
)


def test_get_ollama_reasoning_llm_defaults():
    with patch("langchain_ollama.ChatOllama") as mock_chat_ollama:
        mock_instance = MagicMock()
        mock_chat_ollama.return_value = mock_instance

        llm = get_ollama_reasoning_llm()

        assert llm == mock_instance
        mock_chat_ollama.assert_called_once_with(
            model="qwen2.5:3b",
            base_url="http://localhost:11434",
            temperature=0.1,
        )


def test_get_ollama_reasoning_llm_custom_parameters():
    with patch("langchain_ollama.ChatOllama") as mock_chat_ollama:
        mock_instance = MagicMock()
        mock_chat_ollama.return_value = mock_instance

        llm = get_ollama_reasoning_llm(
            ollama_model="deepseek-r1:7b",
            ollama_url="http://192.168.1.50:11434",
            temperature=0.2,
        )

        assert llm == mock_instance
        mock_chat_ollama.assert_called_once_with(
            model="deepseek-r1:7b",
            base_url="http://192.168.1.50:11434",
            temperature=0.2,
        )


def test_get_ollama_reasoning_llm_fallback_community():
    mock_module = MagicMock()
    mock_chat_class = MagicMock()
    mock_instance = MagicMock()
    mock_chat_class.return_value = mock_instance
    mock_module.ChatOllama = mock_chat_class

    with patch.dict(
        "sys.modules",
        {
            "langchain_ollama": None,
            "langchain_community": MagicMock(),
            "langchain_community.chat_models": mock_module,
        },
    ):
        llm = get_ollama_reasoning_llm(ollama_model="qwen2.5:3b")
        assert llm == mock_instance
        mock_chat_class.assert_called_once_with(
            model="qwen2.5:3b",
            base_url="http://localhost:11434",
            temperature=0.1,
        )


def test_get_ollama_reasoning_llm_import_error():
    with patch.dict("sys.modules", {"langchain_ollama": None, "langchain_community": None, "langchain_community.chat_models": None}):
        llm = get_ollama_reasoning_llm()
        assert llm is None
