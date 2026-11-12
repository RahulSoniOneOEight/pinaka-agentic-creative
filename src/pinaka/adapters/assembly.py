from __future__ import annotations

import subprocess
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..assembly import Segment, build_plan
from ..errors import ConfigurationError
from ..utils import sha256_bytes, sha256_json
from .encode import FFmpegEncodeAdapter


@dataclass(frozen=True)
class AssembledFilm:
    content: bytes
    content_hash: str
    metadata: dict[str, Any]
    command_hash: str
    encoder_version: str
    transition: str
    total_seconds: Decimal


class FFmpegAssemblyAdapter:
    """Trim each shot to its duration and join them with cuts or dissolves."""

    profile_key = "ffmpeg-assembly"

    def __init__(self, executable: str | None = None, timeout_seconds: int = 900):
        self.executable = executable or FFmpegEncodeAdapter.find_executable()
        self.timeout_seconds = timeout_seconds

    def assemble(
        self,
        *,
        segments: list[tuple[Path, Decimal]],
        output_path: Path,
        transition: str = "cut",
        dissolve_seconds: Decimal = Decimal("0.4"),
        fps: int = 24,
        width: int = 1080,
        height: int = 1920,
        starts: list[Decimal] | None = None,
    ) -> AssembledFilm:
        if not segments:
            raise ConfigurationError("Assembly needs at least one clip")
        if starts is not None and len(starts) != len(segments):
            raise ConfigurationError("Assembly needs one in-point per clip")
        plan = build_plan(
            [
                Segment(
                    index=index,
                    path=str(path),
                    duration=duration,
                    start=(starts[index] if starts is not None else Decimal(0)),
                )
                for index, (path, duration) in enumerate(segments)
            ],
            transition=transition,
            dissolve_seconds=dissolve_seconds,
            width=width,
            height=height,
            fps=fps,
        )
        output_path = Path(output_path).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_path.exists():
            raise FileExistsError(f"Refusing to overwrite: {output_path}")
        command = [self.executable, "-y", "-hide_banner", "-loglevel", "error"]
        for path in plan.inputs:
            command.extend(["-i", path])
        command.extend(
            [
                "-filter_complex",
                plan.filter_complex,
                "-map",
                plan.output_label,
                "-r",
                str(fps),
                "-s",
                f"{width}x{height}",
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "18",
                "-pix_fmt",
                "yuv420p",
                "-an",
                "-movflags",
                "+faststart",
                str(output_path),
            ]
        )
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
            check=False,
            shell=False,
        )
        if completed.returncode != 0 or not output_path.is_file():
            detail = (completed.stderr or completed.stdout or "").strip()[:500]
            raise RuntimeError(
                f"Assembly failed with exit {completed.returncode}: {detail}"
            )
        content = output_path.read_bytes()
        metadata = FFmpegEncodeAdapter(self.executable).probe(output_path)
        return AssembledFilm(
            content=content,
            content_hash=sha256_bytes(content),
            metadata=metadata,
            command_hash=sha256_json(
                {
                    "transition": transition,
                    "dissolve_seconds": str(dissolve_seconds),
                    "segments": [
                        {"path": str(path), "duration": str(duration)}
                        for path, duration in segments
                    ],
                    "fps": fps,
                    "width": width,
                    "height": height,
                }
            ),
            encoder_version=FFmpegEncodeAdapter(self.executable).version(),
            transition=transition,
            total_seconds=Decimal(str(metadata.get("duration_seconds", 0))),
        )
