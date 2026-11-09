from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Sequence

from .enums import CapabilityStatus
from .models import CapabilityResult
from .utils import utc_now


def _first_line(value: str) -> str:
    return next(
        (line.strip() for line in value.splitlines() if line.strip()),
        "No version output",
    )


def probe_executable(name: str, command: Sequence[str]) -> CapabilityResult:
    executable = command[0]
    resolved = executable if os.path.isabs(executable) else shutil.which(executable)
    if not resolved:
        return CapabilityResult(
            name=name,
            status=CapabilityStatus.UNAVAILABLE,
            detail=f"Executable not found: {executable}",
            checked_at=utc_now(),
        )
    try:
        completed = subprocess.run(
            [resolved, *command[1:]],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return CapabilityResult(
            name=name,
            status=CapabilityStatus.DEGRADED,
            detail=f"Probe failed: {type(error).__name__}",
            checked_at=utc_now(),
        )
    output = _first_line(completed.stdout or completed.stderr)
    status = (
        CapabilityStatus.WORKING
        if completed.returncode == 0
        else CapabilityStatus.DEGRADED
    )
    return CapabilityResult(
        name=name,
        status=status,
        detail=f"{output} (exit {completed.returncode})",
        checked_at=utc_now(),
    )


def probe_environment(name: str, variable_names: Sequence[str]) -> CapabilityResult:
    configured = any(bool(os.environ.get(variable)) for variable in variable_names)
    return CapabilityResult(
        name=name,
        status=CapabilityStatus.CONFIGURED
        if configured
        else CapabilityStatus.UNAVAILABLE,
        detail=(
            "Credential environment is configured; value not inspected or recorded"
            if configured
            else f"None of the expected environment variables are set: {', '.join(variable_names)}"
        ),
        checked_at=utc_now(),
    )


def probe_all() -> list[CapabilityResult]:
    results = [
        probe_executable("python", (sys.executable, "--version")),
        probe_executable("git", ("git", "--version")),
        probe_executable("ffmpeg", ("ffmpeg", "-version")),
        probe_executable("ffprobe", ("ffprobe", "-version")),
        probe_executable("imagemagick", ("magick", "-version")),
        probe_executable("kinocut", ("kinocut", "--version")),
        probe_environment("fal", ("FAL_KEY", "FAL_API_KEY")),
        probe_environment("penpot_mcp", ("PENPOT_MCP_CONFIGURED",)),
    ]
    return results
