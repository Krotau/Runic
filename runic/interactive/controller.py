from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from enum import Enum

from runic import DefaultError, Err, Ok, Result, Runic, SpellContext

from .models import ChatMessage, InstalledModel, ModelInstallStatus, ModelProvider, ModelReference
from .parsing import parse_model_reference
from .registry import ModelRegistry
from .runners.base import ModelRunner


class InstallDecisionStatus(str, Enum):
    READY = "ready"
    MISSING_RUNNER = "missing_runner"
    UNSUPPORTED = "unsupported"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class InstallModel:
    source: str


@dataclass(frozen=True, slots=True)
class InstallDecision:
    status: InstallDecisionStatus
    reference: ModelReference | None = None
    runner: str | None = None
    message: str = ""


def _decision_error(decision: InstallDecision) -> DefaultError:
    return DefaultError(message=decision.message, code=decision.status.value)


class ModelController:
    def __init__(
        self,
        runtime: Runic,
        registry: ModelRegistry,
        runners: Sequence[ModelRunner] = (),
    ) -> None:
        self._runtime = runtime
        self._registry = registry
        self._runners = tuple(runners)
        self._runners_by_name = {runner.name: runner for runner in self._runners}
        self._runtime.spell(InstallModel)(self._install_spell)

    async def prepare_install(self, source: str) -> InstallDecision:
        match parse_model_reference(source):
            case Err(error=error):
                return InstallDecision(status=InstallDecisionStatus.INVALID, message=error.message)
            case Ok(value=reference):
                compatible_runners = await self._compatible_runners(reference.provider)
                if not compatible_runners:
                    return InstallDecision(
                        status=InstallDecisionStatus.UNSUPPORTED,
                        reference=reference,
                        message=self._unsupported_message(reference.provider),
                    )

                for runner in compatible_runners:
                    if await runner.is_available():
                        return InstallDecision(
                            status=InstallDecisionStatus.READY,
                            reference=reference,
                            runner=runner.name,
                            message=f"Ready to install with {runner.name}.",
                        )

                runner = compatible_runners[0]
                return InstallDecision(
                    status=InstallDecisionStatus.MISSING_RUNNER,
                    reference=reference,
                    runner=runner.name,
                    message=f"{runner.name} is required to install this model.",
                )

        raise AssertionError("Unreachable install decision branch")

    async def install(self, source: str) -> Result[str, DefaultError]:
        decision = await self.prepare_install(source)
        if decision.status is not InstallDecisionStatus.READY:
            return Err(_decision_error(decision))

        try:
            spell_id = await self._runtime.invoke(InstallModel(source=source))
        except Exception as exc:
            return Err(
                DefaultError(
                    message="Failed to schedule model installation.",
                    code="install_schedule_failed",
                    details={"error": str(exc)},
                )
            )

        return Ok(spell_id)

    async def chat(self, model: str, messages: tuple[ChatMessage, ...]) -> AsyncIterator[str]:
        installed = self._registry.get(model)
        if installed.status is not ModelInstallStatus.INSTALLED:
            raise LookupError(f"Model is not installed: {model}")

        runner = await self._runner_for_installed_model(installed)
        if runner is None:
            raise LookupError(f"Runner not available for model: {model}")

        async for chunk in runner.chat(installed.name, messages):
            yield chunk

    async def _install_spell(
        self,
        ctx: SpellContext[InstallModel],
        request: InstallModel,
    ) -> Result[InstalledModel, DefaultError]:
        decision = await self.prepare_install(request.source)
        if decision.status is not InstallDecisionStatus.READY or decision.reference is None:
            return Err(_decision_error(decision))

        runner = self._runner_by_name(decision.runner or "")
        if runner is None or not await runner.is_available():
            missing = InstallDecision(
                status=InstallDecisionStatus.MISSING_RUNNER,
                reference=decision.reference,
                runner=decision.runner,
                message=decision.message or "Required runner is unavailable.",
            )
            return Err(_decision_error(missing))

        result = await runner.install_model(decision.reference, ctx)
        match result:
            case Err(error=error):
                await self._save_install_record(
                    InstalledModel(
                        name=decision.reference.local_name,
                        provider=decision.reference.provider,
                        source=decision.reference.source,
                        runner=runner.name,
                        status=ModelInstallStatus.FAILED,
                        metadata=self._install_metadata(ctx),
                    )
                )
                return Err(error)
            case Ok(value=installed_model):
                saved_model = self._with_install_metadata(installed_model, ctx)
                await self._save_install_record(saved_model)
                return Ok(saved_model)

        raise AssertionError("Unreachable install spell branch")

    async def _compatible_runners(self, provider: ModelProvider) -> tuple[ModelRunner, ...]:
        runners: list[ModelRunner] = []
        for runner in self._runners:
            if self._runner_supports(runner, provider, can_install=True):
                runners.append(runner)
        return tuple(runners)

    async def _runner_for_installed_model(self, model: InstalledModel) -> ModelRunner | None:
        runner = self._runner_by_name(model.runner or "")
        if runner is None:
            return None
        if not self._runner_supports(runner, model.provider, can_chat=True):
            return None
        if not await runner.is_available():
            return None
        return runner

    def _runner_by_name(self, name: str) -> ModelRunner | None:
        return self._runners_by_name.get(name)

    def _runner_supports(
        self,
        runner: ModelRunner,
        provider: ModelProvider,
        *,
        can_install: bool = False,
        can_chat: bool = False,
    ) -> bool:
        for capability in runner.capabilities:
            if capability.provider is not provider:
                continue
            if can_install and not capability.can_install:
                continue
            if can_chat and not capability.can_chat:
                continue
            return True
        return False

    async def _save_install_record(self, model: InstalledModel) -> None:
        self._registry.save(model)

    def _install_metadata(self, ctx: SpellContext[InstallModel]) -> dict[str, str]:
        last_log = self._last_install_log(ctx)
        metadata: dict[str, str] = {}
        if last_log is not None:
            metadata["last_log"] = last_log
        return metadata

    def _with_install_metadata(self, model: InstalledModel, ctx: SpellContext[InstallModel]) -> InstalledModel:
        metadata = dict(model.metadata)
        last_log = self._last_install_log(ctx)
        if last_log is not None:
            metadata["last_log"] = last_log
        return InstalledModel(
            name=model.name,
            provider=model.provider,
            source=model.source,
            runner=model.runner,
            status=model.status,
            metadata=metadata,
        )

    def _last_install_log(self, ctx: SpellContext[InstallModel]) -> str | None:
        # The installed registry record should preserve the latest install log
        # for later inspection.
        if not ctx.record.logs:
            return None
        return ctx.record.logs[-1]

    def _unsupported_message(self, provider: ModelProvider) -> str:
        match provider:
            case ModelProvider.HUGGING_FACE:
                return "No compatible runner is installed for this Hugging Face model."
            case ModelProvider.OLLAMA:
                return "No compatible runner is installed for this Ollama model."
            case _:
                return "No compatible runner is installed for this model."
