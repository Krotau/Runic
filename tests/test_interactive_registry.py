from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from runic.interactive.models import InstalledModel
from runic.interactive.registry import ModelRegistry


class TestInteractiveRegistry(unittest.TestCase):
    def test_save_creates_parent_directories_and_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_path = Path(tmpdir) / "nested" / "registry.json"
            registry = ModelRegistry(path=registry_path)
            registry.installed_models.append(
                InstalledModel(
                    local_name="llama3.2",
                    source_provider="ollama",
                    source_uri="ollama://llama3.2",
                    runner_name="ollama",
                    status="installed",
                    metadata={"size": "3b"},
                )
            )

            registry.save()

            self.assertTrue(registry_path.exists())
            self.assertTrue(registry_path.parent.exists())

            loaded = ModelRegistry.load(registry_path)
            self.assertEqual(1, len(loaded.installed_models))
            self.assertEqual("llama3.2", loaded.installed_models[0].local_name)
            self.assertEqual("installed", loaded.installed_models[0].status)
            self.assertEqual({"size": "3b"}, loaded.installed_models[0].metadata)

    def test_load_accepts_registry_json_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_path = Path(tmpdir) / "registry.json"
            payload = {
                "installed_models": [
                    {
                        "local_name": "phi3",
                        "source_provider": "huggingface",
                        "source_uri": "https://huggingface.co/microsoft/Phi-3-mini-4k-instruct",
                        "runner_name": "ollama",
                        "status": "unavailable",
                        "metadata": {"quantization": "q4"},
                    }
                ]
            }
            registry_path.write_text(json.dumps(payload), encoding="utf-8")

            loaded = ModelRegistry.load(registry_path)

            self.assertEqual(1, len(loaded.installed_models))
            self.assertEqual("phi3", loaded.installed_models[0].local_name)
            self.assertEqual("unavailable", loaded.installed_models[0].status)
            self.assertEqual({"quantization": "q4"}, loaded.installed_models[0].metadata)


if __name__ == "__main__":
    unittest.main()
