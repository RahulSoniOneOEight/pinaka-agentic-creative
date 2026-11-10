from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from ..errors import ConfigurationError
from ..utils import sha256_bytes, sha256_json


@dataclass(frozen=True)
class CapturedFrame:
    index: int
    time_seconds: str
    content: bytes
    content_hash: str


@dataclass(frozen=True)
class FrameCaptureResult:
    frames: tuple[CapturedFrame, ...]
    renderer_version: str
    command_hash: str


class FrameCaptureAdapter(Protocol):
    profile_key: str

    def capture(
        self,
        *,
        html_path: Path,
        frame_count: int,
        fps: int,
        width: int,
        height: int,
    ) -> FrameCaptureResult: ...


def frame_time(index: int, fps: int) -> Decimal:
    if index < 0:
        raise ValueError("Frame index must be non-negative")
    if fps < 1:
        raise ValueError("Frame rate must be positive")
    return Decimal(index) / Decimal(fps)


class ChromeFrameCapture:
    """Deterministic local frame capture through headless Chrome or Edge."""

    profile_key = "chrome-headless-capture"

    def __init__(self, executable: str | None = None, timeout_seconds: int = 60):
        self.executable = executable or self.find_executable()
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def find_executable() -> str:
        candidates = [
            os.environ.get("PINAKA_CHROME"),
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        ]
        for candidate in candidates:
            if candidate and Path(candidate).is_file():
                return candidate
        for name in ("chrome", "chrome.exe", "msedge", "msedge.exe"):
            found = shutil.which(name)
            if found:
                return found
        raise ConfigurationError(
            "No Chrome or Edge executable found. Set PINAKA_CHROME to the browser path."
        )

    def version(self) -> str:
        try:
            completed = subprocess.run(
                [self.executable, "--version"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
                shell=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise ConfigurationError(
                f"Browser version probe failed: {type(error).__name__}"
            ) from error
        output = (completed.stdout or completed.stderr).strip()
        if completed.returncode != 0 or not output:
            raise ConfigurationError(
                f"Browser version probe exited {completed.returncode}"
            )
        return output.splitlines()[0]

    def capture(
        self,
        *,
        html_path: Path,
        frame_count: int,
        fps: int,
        width: int,
        height: int,
    ) -> FrameCaptureResult:
        if frame_count < 1:
            raise ValueError("Frame count must be positive")
        if fps < 1:
            raise ValueError("Frame rate must be positive")
        html_path = html_path.resolve()
        if not html_path.is_file():
            raise FileNotFoundError(f"Animatic HTML not found: {html_path}")
        version = self.version()
        frames: list[CapturedFrame] = []
        with tempfile.TemporaryDirectory(prefix="pinaka-frames-") as temp:
            temp_path = Path(temp)
            for index in range(frame_count):
                seconds = frame_time(index, fps)
                output = temp_path / f"frame-{index:04d}.png"
                profile = temp_path / f"profile-{index:04d}"
                url = f"{html_path.as_uri()}?t={format(seconds.normalize(), 'f')}"
                command = [
                    self.executable,
                    "--headless=new",
                    "--disable-gpu",
                    "--hide-scrollbars",
                    "--force-device-scale-factor=1",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--disable-extensions",
                    f"--user-data-dir={profile}",
                    f"--window-size={width},{height}",
                    f"--screenshot={output}",
                    url,
                ]
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                    check=False,
                    shell=False,
                )
                if completed.returncode != 0 or not output.is_file():
                    detail = (completed.stderr or completed.stdout or "").strip()[:300]
                    raise RuntimeError(
                        f"Browser frame {index} failed with exit "
                        f"{completed.returncode}: {detail}"
                    )
                content = output.read_bytes()
                if not content.startswith(b"\x89PNG\r\n\x1a\n"):
                    raise RuntimeError(f"Browser frame {index} is not a PNG")
                frames.append(
                    CapturedFrame(
                        index=index,
                        time_seconds=format(seconds.normalize(), "f"),
                        content=content,
                        content_hash=sha256_bytes(content),
                    )
                )
        command_hash = sha256_json(
            {
                "executable": self.executable,
                "version": version,
                "width": width,
                "height": height,
                "frame_count": frame_count,
                "fps": fps,
            }
        )
        return FrameCaptureResult(
            frames=tuple(frames),
            renderer_version=version,
            command_hash=command_hash,
        )
