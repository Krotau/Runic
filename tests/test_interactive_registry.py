from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from runic.interactive.models import InstalledModel, ModelInstallStatus, ModelProvider
from runic.interactive.registry import ModelRegistry, default_registry_path


class TestInteractiveRegistry(unittest.TestCase):
    def test_registry_round_trips_models(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "models.json"
            registry = ModelRegistry(path)
            model = InstalledModel(
                name="llama3.2",
                provider=ModelProvider.OLLAMA,
                source="ollama://llama3.2",
                runner="ollama",
                status=ModelInstallStatus.INSTALLED,
                metadata={"size": "2GB"},
            )

            registry.save(model)

            loaded = ModelRegistry(path)
            self.assertEqual([model], loaded.list())
            self.assertEqual(model, loaded.get("llama3.2"))

    def test_registry_creates_parent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "nested" / "models.json"
            registry = ModelRegistry(path)

            registry.save(
                InstalledModel(
                    name="pending",
                    provider=ModelProvider.HUGGING_FACE,
                    source="https://huggingface.co/org/model",
                    runner=None,
                    status=ModelInstallStatus.UNAVAILABLE,
                )
            )

            self.assertTrue(path.exists())

    def test_default_registry_path_uses_config_home(self) -> None:
        path = default_registry_path({"XDG_CONFIG_HOME": "/tmp/runic-test-config"})

        self.assertEqual(Path("/tmp/runic-test-config/runic/models.json"), path)
