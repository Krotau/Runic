from __future__ import annotations

import unittest

from runic.interactive.models import ChatMessage, ModelProvider
from runic.interactive.parsing import parse_model_reference
from runic.result import Err, Ok


class TestInteractiveParsing(unittest.TestCase):
    def test_parse_plain_ollama_name(self) -> None:
        result = parse_model_reference("llama3.2")

        self.assertIsInstance(result, Ok)
        assert isinstance(result, Ok)
        self.assertEqual(ModelProvider.OLLAMA, result.value.provider)
        self.assertEqual("llama3.2", result.value.model)
        self.assertEqual("llama3.2", result.value.local_name)

    def test_parse_ollama_uri(self) -> None:
        result = parse_model_reference("ollama://llama3.2:1b")

        self.assertIsInstance(result, Ok)
        assert isinstance(result, Ok)
        self.assertEqual(ModelProvider.OLLAMA, result.value.provider)
        self.assertEqual("llama3.2:1b", result.value.model)
        self.assertEqual("ollama://llama3.2:1b", result.value.source)

    def test_parse_ollama_library_url(self) -> None:
        result = parse_model_reference("https://ollama.com/library/llama3.2")

        self.assertIsInstance(result, Ok)
        assert isinstance(result, Ok)
        self.assertEqual(ModelProvider.OLLAMA, result.value.provider)
        self.assertEqual("llama3.2", result.value.model)

    def test_parse_ollama_library_url_ignores_query(self) -> None:
        result = parse_model_reference("https://ollama.com/library/llama3.2?x=1")

        self.assertIsInstance(result, Ok)
        assert isinstance(result, Ok)
        self.assertEqual(ModelProvider.OLLAMA, result.value.provider)
        self.assertEqual("llama3.2", result.value.model)
        self.assertEqual("ollama://llama3.2", result.value.source)

    def test_parse_ollama_library_subpath_normalizes_to_base_model(self) -> None:
        result = parse_model_reference("https://ollama.com/library/llama3.2/blobs/abc")

        self.assertIsInstance(result, Ok)
        assert isinstance(result, Ok)
        self.assertEqual(ModelProvider.OLLAMA, result.value.provider)
        self.assertEqual("llama3.2", result.value.model)
        self.assertEqual("ollama://llama3.2", result.value.source)

    def test_parse_hugging_face_url(self) -> None:
        result = parse_model_reference("https://huggingface.co/meta-llama/Llama-3.2-1B")

        self.assertIsInstance(result, Ok)
        assert isinstance(result, Ok)
        self.assertEqual(ModelProvider.HUGGING_FACE, result.value.provider)
        self.assertEqual("meta-llama/Llama-3.2-1B", result.value.model)
        self.assertEqual("meta-llama-Llama-3.2-1B", result.value.local_name)

    def test_parse_hugging_face_url_ignores_query_and_fragment(self) -> None:
        result = parse_model_reference(
            "https://huggingface.co/meta-llama/Llama-3.2-1B/tree/main?foo=bar#section"
        )

        self.assertIsInstance(result, Ok)
        assert isinstance(result, Ok)
        self.assertEqual(ModelProvider.HUGGING_FACE, result.value.provider)
        self.assertEqual("meta-llama/Llama-3.2-1B", result.value.model)
        self.assertEqual("meta-llama-Llama-3.2-1B", result.value.local_name)
        self.assertEqual("https://huggingface.co/meta-llama/Llama-3.2-1B", result.value.source)

    def test_parse_rejects_empty_input(self) -> None:
        result = parse_model_reference(" ")

        self.assertIsInstance(result, Err)
        assert isinstance(result, Err)
        self.assertEqual("invalid_model_reference", result.error.code)

    def test_chat_message_dataclass_fields(self) -> None:
        message = ChatMessage(role="user", content="hi")

        self.assertEqual(ChatMessage(role="user", content="hi"), message)
        self.assertEqual("user", message.role)
        self.assertEqual("hi", message.content)
