from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import InstalledModel


@dataclass(slots=True)
class ModelRegistry:
    path: Path
    installed_models: list[InstalledModel] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "ModelRegistry":
        if not path.exists():
            return cls(path=path)

        payload = json.loads(path.read_text(encoding="utf-8"))
        models = [InstalledModel.from_dict(item) for item in payload.get("installed_models", [])]
        return cls(path=path, installed_models=models)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.to_dict(), indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "installed_models": [model.to_dict() for model in self.installed_models],
        }
