from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping


class ModelProvider(str, Enum):
    OLLAMA = "ollama"
    HUGGING_FACE = "hugging_face"


class ModelInstallStatus(str, Enum):
    PENDING = "pending"
    INSTALLED = "installed"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class ModelReference:
    provider: ModelProvider
    source: str
    model: str
    local_name: str
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class InstalledModel:
    name: str
    provider: ModelProvider
    source: str
    runner: str | None
    status: ModelInstallStatus
    metadata: Mapping[str, str] = field(default_factory=dict)
