from __future__ import annotations

from runic.errors import DefaultError
from runic.result import Err, Ok, Result

from .models import ModelProvider, ModelReference


def _invalid_reference() -> Err[DefaultError]:
    return Err(DefaultError(message="Invalid model reference.", code="invalid_model_reference"))


def _ollama_local_name(model: str) -> str:
    return model


def _hugging_face_local_name(model: str) -> str:
    return model.replace("/", "-")


def parse_model_reference(source: str) -> Result[ModelReference, DefaultError]:
    cleaned = source.strip()
    if not cleaned:
        return _invalid_reference()

    if cleaned.startswith("ollama://"):
        model = cleaned.removeprefix("ollama://")
        if not model:
            return _invalid_reference()
        return Ok(
            ModelReference(
                provider=ModelProvider.OLLAMA,
                source=cleaned,
                model=model,
                local_name=_ollama_local_name(model),
            )
        )

    if cleaned.startswith("https://ollama.com/library/"):
        model = cleaned.removeprefix("https://ollama.com/library/")
        if not model:
            return _invalid_reference()
        return Ok(
            ModelReference(
                provider=ModelProvider.OLLAMA,
                source=f"ollama://{model}",
                model=model,
                local_name=_ollama_local_name(model),
            )
        )

    if cleaned.startswith("https://huggingface.co/"):
        model = cleaned.removeprefix("https://huggingface.co/")
        if not model or "/" not in model:
            return _invalid_reference()
        return Ok(
            ModelReference(
                provider=ModelProvider.HUGGING_FACE,
                source=cleaned,
                model=model,
                local_name=_hugging_face_local_name(model),
            )
        )

    if "://" not in cleaned:
        model = cleaned
        return Ok(
            ModelReference(
                provider=ModelProvider.OLLAMA,
                source=f"ollama://{model}",
                model=model,
                local_name=_ollama_local_name(model),
            )
        )

    return _invalid_reference()
