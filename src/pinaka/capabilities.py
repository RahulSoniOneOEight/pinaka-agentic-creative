from __future__ import annotations

import glob
import importlib.util
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from .enums import CapabilityStatus
from .models import CapabilityResult
from .utils import utc_now


def _first_line(value: str) -> str:
    return next(
        (line.strip() for line in value.splitlines() if line.strip()),
        "No version output",
    )


def probe_executable(
    name: str,
    command: Sequence[str],
    *,
    search_globs: Sequence[str] = (),
) -> CapabilityResult:
    executable = command[0]
    resolved = executable if os.path.isabs(executable) else shutil.which(executable)
    if not resolved:
        for pattern in search_globs:
            matches = sorted(glob.glob(os.path.expandvars(pattern)))
            if matches:
                resolved = matches[-1]
                break
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


def probe_presence(
    name: str, search_globs: Sequence[str], *, label: str | None = None
) -> CapabilityResult:
    """Confirm that a GUI application is installed without launching it."""

    for pattern in search_globs:
        matches = sorted(glob.glob(os.path.expandvars(pattern)))
        if matches:
            return CapabilityResult(
                name=name,
                status=CapabilityStatus.CONFIGURED,
                detail=f"{label or name} installed at {matches[-1]}; version not launched",
                checked_at=utc_now(),
            )
    return CapabilityResult(
        name=name,
        status=CapabilityStatus.UNAVAILABLE,
        detail=f"{label or name} executable was not found in the expected paths",
        checked_at=utc_now(),
    )


def probe_secret(
    name: str, variable_names: Sequence[str], default_file: str
) -> CapabilityResult:
    if any(bool(os.environ.get(variable)) for variable in variable_names):
        return CapabilityResult(
            name=name,
            status=CapabilityStatus.CONFIGURED,
            detail="Credential environment is configured; value not inspected",
            checked_at=utc_now(),
        )
    path = Path(os.path.expandvars(default_file)).expanduser()
    if path.is_file():
        return CapabilityResult(
            name=name,
            status=CapabilityStatus.CONFIGURED,
            detail=f"Credential file exists at {path}; value not inspected",
            checked_at=utc_now(),
        )
    return CapabilityResult(
        name=name,
        status=CapabilityStatus.UNAVAILABLE,
        detail="No credential environment or key file is configured",
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


def probe_python_module(name: str, module: str) -> CapabilityResult:
    try:
        available = importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        available = False
    return CapabilityResult(
        name=name,
        status=CapabilityStatus.WORKING if available else CapabilityStatus.UNAVAILABLE,
        detail=(
            f"Python module {module!r} is importable; version not inspected"
            if available
            else f"Python module {module!r} is not installed"
        ),
        checked_at=utc_now(),
    )


def probe_ffprobe_static() -> CapabilityResult:
    try:
        from .media_probe import find_ffprobe
    except ImportError:
        return CapabilityResult(
            name="ffprobe_static",
            status=CapabilityStatus.UNAVAILABLE,
            detail="media probe module is unavailable",
            checked_at=utc_now(),
        )
    executable = find_ffprobe()
    if not executable:
        return CapabilityResult(
            name="ffprobe_static",
            status=CapabilityStatus.UNAVAILABLE,
            detail="No ffprobe binary found via static-ffmpeg or PATH",
            checked_at=utc_now(),
        )
    completed = subprocess.run(
        [executable, "-version"],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
        shell=False,
    )
    output = _first_line(completed.stdout or completed.stderr)
    return CapabilityResult(
        name="ffprobe_static",
        status=CapabilityStatus.WORKING
        if completed.returncode == 0
        else CapabilityStatus.DEGRADED,
        detail=f"{output} (exit {completed.returncode})",
        checked_at=utc_now(),
    )


def probe_mediainfo() -> CapabilityResult:
    try:
        from pymediainfo import MediaInfo
    except ImportError:
        return CapabilityResult(
            name="mediainfo",
            status=CapabilityStatus.UNAVAILABLE,
            detail="pymediainfo is not installed",
            checked_at=utc_now(),
        )
    try:
        available = bool(MediaInfo.can_parse())
    except (OSError, RuntimeError) as error:
        return CapabilityResult(
            name="mediainfo",
            status=CapabilityStatus.DEGRADED,
            detail=f"MediaInfo library probe failed: {type(error).__name__}",
            checked_at=utc_now(),
        )
    return CapabilityResult(
        name="mediainfo",
        status=CapabilityStatus.WORKING if available else CapabilityStatus.UNAVAILABLE,
        detail=(
            "pymediainfo and the MediaInfo library are available"
            if available
            else "pymediainfo is installed but the MediaInfo library is unavailable"
        ),
        checked_at=utc_now(),
    )


def probe_penpot_mcp() -> CapabilityResult:
    configured = bool(os.environ.get("PENPOT_MCP_CONFIGURED"))
    return CapabilityResult(
        name="penpot_mcp",
        status=CapabilityStatus.CONFIGURED
        if configured
        else CapabilityStatus.UNAVAILABLE,
        detail=(
            "Optional environment flag is set; the live MCP connection is verified separately "
            "in the OpenCode session"
            if configured
            else "Optional PENPOT_MCP_CONFIGURED flag is not set; the live MCP connection is "
            "verified separately in the OpenCode session"
        ),
        checked_at=utc_now(),
    )


def probe_imageio_ffmpeg() -> CapabilityResult:
    try:
        import imageio_ffmpeg
    except ImportError:
        return CapabilityResult(
            name="ffmpeg_imageio",
            status=CapabilityStatus.UNAVAILABLE,
            detail="Optional imageio-ffmpeg package is not installed",
            checked_at=utc_now(),
        )
    try:
        executable = imageio_ffmpeg.get_ffmpeg_exe()
        completed = subprocess.run(
            [executable, "-version"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return CapabilityResult(
            name="ffmpeg_imageio",
            status=CapabilityStatus.DEGRADED,
            detail=f"Bundled FFmpeg probe failed: {type(error).__name__}",
            checked_at=utc_now(),
        )
    output = _first_line(completed.stdout or completed.stderr)
    return CapabilityResult(
        name="ffmpeg_imageio",
        status=CapabilityStatus.WORKING
        if completed.returncode == 0
        else CapabilityStatus.DEGRADED,
        detail=f"{output} (exit {completed.returncode})",
        checked_at=utc_now(),
    )


def probe_comfyui_local() -> CapabilityResult:
    root = Path(os.environ.get("PINAKA_COMFYUI_HOME", "D:/pinaka-tools/ComfyUI"))
    main = root / "main.py"
    if not main.is_file():
        return CapabilityResult(
            name="comfyui_local",
            status=CapabilityStatus.UNAVAILABLE,
            detail=f"ComfyUI source was not found at {root}",
            checked_at=utc_now(),
        )
    models = sorted((root / "models" / "checkpoints").glob("*.safetensors"))
    return CapabilityResult(
        name="comfyui_local",
        status=CapabilityStatus.WORKING if models else CapabilityStatus.CONFIGURED,
        detail=(
            f"ComfyUI source at {root} with {len(models)} checkpoint(s)"
            if models
            else f"ComfyUI source at {root}; no checkpoint is installed"
        ),
        checked_at=utc_now(),
    )


def probe_all() -> list[CapabilityResult]:
    results = [
        probe_executable("python", (sys.executable, "--version")),
        probe_executable("git", ("git", "--version")),
        probe_executable("ffmpeg", ("ffmpeg", "-version")),
        probe_executable("ffprobe", ("ffprobe", "-version")),
        probe_ffprobe_static(),
        probe_mediainfo(),
        probe_imageio_ffmpeg(),
        probe_executable("imagemagick", ("magick", "-version")),
        probe_executable("mediainfo_cli", ("mediainfo", "--Version")),
        probe_executable("tesseract", ("tesseract", "--version")),
        probe_executable("kinocut", ("kinocut", "--version")),
        probe_executable("kdenlive", ("kdenlive", "--version")),
        probe_executable(
            "inkscape",
            ("inkscape", "--version"),
            search_globs=("%ProgramFiles%/Inkscape/bin/inkscape.exe",),
        ),
        probe_executable(
            "gimp",
            ("gimp-console-3.2.exe", "--version"),
            search_globs=("%LOCALAPPDATA%/Programs/GIMP */bin/gimp-console-*.exe",),
        ),
        probe_presence(
            "krita",
            ("%ProgramFiles%/Krita*/bin/krita.exe",),
            label="Krita",
        ),
        probe_executable(
            "blender",
            ("blender", "--version"),
            search_globs=("%ProgramFiles%/Blender Foundation/Blender */blender.exe",),
        ),
        probe_presence(
            "natron",
            (
                "%ProgramFiles%/Natron*/Natron.exe",
                "%ProgramFiles%/INRIA/Natron*/Natron.exe",
                "%LOCALAPPDATA%/Programs/Natron*/Natron.exe",
                "D:/pinaka-tools/natron/Natron*/bin/Natron.exe",
            ),
            label="Natron",
        ),
        probe_presence(
            "synfig",
            ("%ProgramFiles%/Synfig*/bin/synfig.exe",),
            label="Synfig",
        ),
        probe_presence(
            "audacity",
            (
                "%ProgramFiles%/Audacity*/audacity.exe",
                "%ProgramFiles%/Audacity*/bin/Audacity*.exe",
                "%LOCALAPPDATA%/Programs/Audacity/audacity.exe",
            ),
            label="Audacity",
        ),
        probe_presence(
            "opentoonz",
            ("%ProgramFiles%/OpenToonz/OpenToonz.exe",),
            label="OpenToonz",
        ),
        probe_executable("qctools", ("qcli", "--version")),
        probe_executable("mediaconch", ("MediaConch", "--Version")),
        probe_executable("node", ("node", "--version")),
        probe_python_module("pillow", "PIL"),
        probe_python_module("opencv", "cv2"),
        probe_python_module("numpy", "numpy"),
        probe_python_module("playwright", "playwright"),
        probe_python_module("kokoro", "kokoro_onnx"),
        probe_python_module("piper", "piper"),
        probe_python_module("whisper", "whisper"),
        probe_python_module("faster_whisper", "faster_whisper"),
        probe_python_module("manim", "manim"),
        probe_python_module("torch", "torch"),
        probe_python_module("diffusers", "diffusers"),
        probe_environment("comfyui", ("COMFYUI_URL",)),
        probe_comfyui_local(),
        probe_secret(
            "gemini",
            ("GEMINI_API_KEY",),
            "%USERPROFILE%/.config/opencode/secrets/gemini-api-key",
        ),
        probe_secret(
            "freesound",
            ("FREESOUND_API_KEY",),
            "%USERPROFILE%/.config/opencode/secrets/freesound-api-key",
        ),
        probe_environment("openai", ("OPENAI_API_KEY",)),
        probe_environment("deepseek", ("DEEPSEEK_API_KEY",)),
        probe_secret(
            "fal",
            ("FAL_KEY", "FAL_API_KEY"),
            "%USERPROFILE%/.config/opencode/secrets/fal-api-key",
        ),
        probe_penpot_mcp(),
    ]
    return results
