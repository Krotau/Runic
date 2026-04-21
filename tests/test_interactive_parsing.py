from __future__ import annotations

import unittest

from runic import Err, Ok
from runic.errors import DefaultError
from runic.interactive.parsing import parse_model_reference


class TestInteractiveParsing(unittest.TestCase):
    def test_parses_plain_ollama_model_name(self) -> None:
        result = parse_model_reference("llama3.2")

        self.assertIsInstance(result, Ok)
        self.assertEqual("ollama", result.value.provider)
        self.assertEqual("llama3.2", result.value.name)
        self.assertEqual("ollama://llama3.2", result.value.source_uri)

    def test_parses_ollama_library_url(self) -> None:
        result = parse_model_reference("https://ollama.com/library/llama3.2")

        self.assertIsInstance(result, Ok)
        self.assertEqual("ollama", result.value.provider)
        self.assertEqual("llama3.2", result.value.name)
        self.assertEqual("ollama://llama3.2", result.value.source_uri)

    def test_parses_hugging_face_url(self) -> None:
        result = parse_model_reference("https://huggingface.co/meta-llama/Llama-3.2-3B-Instruct")

        self.assertIsInstance(result, Ok)
        self.assertEqual("huggingface", result.value.provider)
        self.assertEqual("meta-llama/Llama-3.2-3B-Instruct", result.value.name)
        self.assertEqual(
            "https://huggingface.co/meta-llama/Llama-3.2-3B-Instruct",
            result.value.source_uri,
        )

    def test_rejects_blank_input_with_default_error(self) -> None:
        result = parse_model_reference("   ")

        self.assertIsInstance(result, Err)
        self.assertIsInstance(result.error, DefaultError)
        self.assertEqual("Model reference is required.", result.error.message)


if __name__ == "__main__":
    unittest.main()
