from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum

LOG_PREFIX = "runic-install:"


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


def format_install_line(update: InstallStatusUpdate, *, width: int = 14) -> str:
    if update.phase is InstallPhase.CONNECTING:
        return "connecting.... connected!" if update.state is InstallPhaseState.DONE else "connecting...."
    if update.phase is InstallPhase.DOWNLOADING:
        ratio = min(1.0, max(0.0, update.progress or 0.0))
        filled = round(ratio * width)
        return f"downloading... [{'#' * filled}{'_' * (width - filled)}] {round(ratio * 100)}%"
    if update.phase is InstallPhase.VERIFYING:
        return "verifying.... verified!" if update.state is InstallPhaseState.DONE else "verifying...."
    if update.phase is InstallPhase.INSTALLING:
        return "installing.... done!" if update.state is InstallPhaseState.DONE else "installing...."
    if update.phase is InstallPhase.FAILED:
        return f"failed: {update.detail}" if update.detail else "failed"
    return "installed"
