from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Mapping

from .models import InstalledModel, ModelInstallStatus, ModelProvider


def default_registry_path(env: Mapping[str, str] = os.environ) -> Path:
    config_home = Path(env.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return config_home / "runic" / "models.json"


class ModelRegistry:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._models = self._load_models()

    def _load_models(self) -> list[InstalledModel]:
        if not self.path.exists():
            return []

        payload = json.loads(self.path.read_text(encoding="utf-8"))
        models = payload.get("models", [])
        return [self._decode_model(item) for item in models]

    def _decode_model(self, data: dict[str, object]) -> InstalledModel:
        return InstalledModel(
            name=str(data["name"]),
            provider=ModelProvider(str(data["provider"])),
            source=str(data["source"]),
            runner=None if data.get("runner") is None else str(data["runner"]),
            status=ModelInstallStatus(str(data["status"])),
            metadata=dict(data.get("metadata", {})),
        )

    def _encode_model(self, model: InstalledModel) -> dict[str, object]:
        return {
            "name": model.name,
            "provider": model.provider.value,
            "source": model.source,
            "runner": model.runner,
            "status": model.status.value,
            "metadata": dict(model.metadata),
        }

    def save(self, model: InstalledModel) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._models = [existing for existing in self._models if existing.name != model.name]
        self._models.append(model)
        payload = {"models": [self._encode_model(existing) for existing in self._models]}
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def list(self) -> list[InstalledModel]:
        return list(self._models)

    def get(self, name: str) -> InstalledModel:
        for model in self._models:
            if model.name == name:
                return model
        raise KeyError(name)
