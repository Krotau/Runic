from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal, TypeAlias

ModelProvider: TypeAlias = Literal["ollama", "huggingface"]
InstallStatus: TypeAlias = Literal["installed", "unavailable", "pending", "failed"]


@dataclass(slots=True, frozen=True)
class ModelReference:
    provider: ModelProvider
    name: str
    source_uri: str


@dataclass(slots=True, frozen=True)
class RunnerCapability:
    name: str
    can_install: tuple[str, ...] = ()
    can_run: tuple[str, ...] = ()


@dataclass(slots=True)
class InstalledModel:
    local_name: str
    source_provider: ModelProvider
    source_uri: str
    runner_name: str
    status: InstallStatus = "pending"
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "InstalledModel":
        return cls(
            local_name=data["local_name"],
            source_provider=data["source_provider"],
            source_uri=data["source_uri"],
            runner_name=data["runner_name"],
            status=data.get("status", "pending"),
            metadata=dict(data.get("metadata", {})),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
