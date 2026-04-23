from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum

LOG_PREFIX = "runic-install:"
SPINNER_FRAMES = ("⠾", "⠽", "⠻", "⠯", "⠷")


class InstallPhase(str, Enum):
    CONNECTING = "connecting"
    DOWNLOADING = "downloading"
    VERIFYING = "verifying"
    INSTALLING = "installing"
    DONE = "done"
    FAILED = "failed"


class InstallPhaseState(str, Enum):
    ACTIVE = "active"
    DONE = "done"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class InstallStatusUpdate:
    phase: InstallPhase
    state: InstallPhaseState
    detail: str = ""
    progress: float | None = None


def encode_install_status(update: InstallStatusUpdate) -> str:
    payload = {
        "phase": update.phase.value,
        "state": update.state.value,
        "detail": update.detail,
        "progress": update.progress,
    }
    return f"{LOG_PREFIX}{json.dumps(payload, separators=(',', ':'))}"


def is_install_status_log(message: str) -> bool:
    return message.startswith(LOG_PREFIX)


def parse_install_status(message: str) -> InstallStatusUpdate | None:
    if not is_install_status_log(message):
        return None
    payload = json.loads(message.removeprefix(LOG_PREFIX))
    if not isinstance(payload, dict):
        return None
    try:
        progress = payload.get("progress")
        return InstallStatusUpdate(
            phase=InstallPhase(str(payload["phase"])),
            state=InstallPhaseState(str(payload["state"])),
            detail=str(payload.get("detail", "")),
            progress=float(progress) if progress is not None else None,
        )
    except (KeyError, TypeError, ValueError):
        return None


def spinner_frame(step: int) -> str:
    return SPINNER_FRAMES[step % len(SPINNER_FRAMES)]


def _with_spinner(text: str, update: InstallStatusUpdate, spinner: str | None) -> str:
    if spinner is None or update.state is not InstallPhaseState.ACTIVE:
        return text
    if update.phase not in {
        InstallPhase.CONNECTING,
        InstallPhase.DOWNLOADING,
        InstallPhase.VERIFYING,
        InstallPhase.INSTALLING,
    }:
        return text
    return f"{spinner} {text}"


def format_install_line(update: InstallStatusUpdate, *, width: int = 14, spinner: str | None = None) -> str:
    if update.phase is InstallPhase.CONNECTING:
        text = "connecting.... connected!" if update.state is InstallPhaseState.DONE else "connecting...."
        return _with_spinner(text, update, spinner)
    if update.phase is InstallPhase.DOWNLOADING:
        ratio = min(1.0, max(0.0, update.progress or 0.0))
        filled = round(ratio * width)
        return _with_spinner(
            f"downloading... [{'#' * filled}{'_' * (width - filled)}] {round(ratio * 100)}%",
            update,
            spinner,
        )
    if update.phase is InstallPhase.VERIFYING:
        text = "verifying.... verified!" if update.state is InstallPhaseState.DONE else "verifying...."
        return _with_spinner(text, update, spinner)
    if update.phase is InstallPhase.INSTALLING:
        text = "installing.... done!" if update.state is InstallPhaseState.DONE else "installing...."
        return _with_spinner(text, update, spinner)
    if update.phase is InstallPhase.FAILED:
        return f"failed: {update.detail}" if update.detail else "failed"
    return "installed"
