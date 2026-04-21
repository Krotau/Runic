from __future__ import annotations

from urllib.parse import urlsplit

from runic import Err, Ok, Result
from runic.errors import DefaultError

from .models import ModelReference


def _error(message: str) -> Err[DefaultError]:
    return Err(DefaultError(message=message))


def _parse_ollama_reference(text: str) -> ModelReference | None:
    if text.startswith("ollama://"):
        name = text.removeprefix("ollama://").strip()
        if not name:
            return None
        return ModelReference(provider="ollama", name=name, source_uri=f"ollama://{name}")

    if text.startswith("http://") or text.startswith("https://"):
        parsed = urlsplit(text)
        if parsed.netloc != "ollama.com":
            return None
        path = parsed.path.strip("/")
        if not path.startswith("library/"):
            return None
        name = path.removeprefix("library/").strip()
        if not name:
            return None
        return ModelReference(provider="ollama", name=name, source_uri=f"ollama://{name}")

    if "://" in text:
        return None

    return ModelReference(provider="ollama", name=text, source_uri=f"ollama://{text}")


def _parse_hugging_face_reference(text: str) -> ModelReference | None:
    if not (text.startswith("http://") or text.startswith("https://")):
        return None

    parsed = urlsplit(text)
    if parsed.netloc != "huggingface.co":
        return None

    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        return None

    repo_id = f"{parts[0]}/{parts[1]}"
    return ModelReference(
        provider="huggingface",
        name=repo_id,
        source_uri=f"https://huggingface.co/{repo_id}",
    )


def parse_model_reference(text: str) -> Result[ModelReference, DefaultError]:
    cleaned = text.strip()
    if not cleaned:
        return _error("Model reference is required.")

    ollama_reference = _parse_ollama_reference(cleaned)
    if ollama_reference is not None:
        return Ok(ollama_reference)

    hugging_face_reference = _parse_hugging_face_reference(cleaned)
    if hugging_face_reference is not None:
        return Ok(hugging_face_reference)

    return _error("Unsupported model reference.")
