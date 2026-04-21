from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from runic.errors import DefaultError
from runic.result import Err, Ok, Result

from .models import ModelProvider, ModelReference


def _invalid_reference() -> Err[DefaultError]:
    return Err(DefaultError(message="Invalid model reference.", code="invalid_model_reference"))


def _ollama_local_name(model: str) -> str:
    return model


def _hugging_face_local_name(model: str) -> str:
    return model.replace("/", "-")


def _path_segments(url: str) -> tuple[str, ...]:
    return tuple(segment for segment in urlsplit(url).path.split("/") if segment)


def _canonical_http_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


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

    if cleaned.startswith("https://ollama.com/"):
        segments = _path_segments(cleaned)
        if len(segments) < 2 or segments[0] != "library":
            return _invalid_reference()
        model = segments[1]
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
        canonical_source = _canonical_http_url(cleaned)
        segments = _path_segments(cleaned)
        if len(segments) < 2:
            return _invalid_reference()
        model = f"{segments[0]}/{segments[1]}"
        return Ok(
            ModelReference(
                provider=ModelProvider.HUGGING_FACE,
                source=canonical_source,
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
