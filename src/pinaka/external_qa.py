from __future__ import annotations

import gzip
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Literal
from xml.etree import ElementTree

from pydantic import Field

from .models import StrictModel
from .utils import sha256_file, sha256_json, utc_now

ToolStatus = Literal["pass", "warn", "fail", "unavailable"]


class QCToolsSummary(StrictModel):
    frames: int = 0
    parsed: bool = False
    detail: str


class ExternalCheck(StrictModel):
    tool: str
    status: ToolStatus
    executable: str | None = None
    elapsed_seconds: float | None = None
    output_path: str | None = None
    output_sha256: str | None = None
    detail: str
    evidence: dict[str, Any] = Field(default_factory=dict)


class ExternalQAReport(StrictModel):
    schema_version: str = "external-qa-report/v1"
    generated_at: str
    video_path: str
    video_sha256: str
    overall: Literal["pass", "warn", "fail"]
    checks: tuple[ExternalCheck, ...]
    manifest_hash: str


def parse_qctools_report(path: Path | str) -> QCToolsSummary:
    report = Path(path)
    if not report.is_file() or report.stat().st_size == 0:
        return QCToolsSummary(detail="QCTools report is missing or empty")
    try:
        with gzip.open(report, "rb") as handle:
            root = ElementTree.parse(handle).getroot()
    except (OSError, ElementTree.ParseError) as error:
        return QCToolsSummary(
            detail=f"QCTools report could not be parsed: {type(error).__name__}"
        )
    frames = sum(1 for element in root.iter() if element.tag.endswith("frame"))
    return QCToolsSummary(
        frames=frames,
        parsed=True,
        detail=f"Parsed QCTools XML with {frames} frame element(s)",
    )


def summarize_mediaconch(stdout: str, returncode: int) -> tuple[ToolStatus, str]:
    if returncode != 0:
        return "fail", f"MediaConch exited with code {returncode}"
    if "N/A" in stdout:
        return (
            "warn",
            "MediaConch parsed the file but returned N/A because no policy was supplied",
        )
    if "fail" in stdout.casefold():
        return "fail", "MediaConch reported a policy failure"
    return "pass", "MediaConch parsed the file"


def _find(name: str, candidates: tuple[str, ...]) -> str | None:
    if name:
        resolved = shutil.which(name)
        if resolved:
            return resolved
    for candidate in candidates:
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return None


def _run(command: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        shell=False,
    )


def run_qctools(
    video_path: Path | str,
    output_dir: Path | str,
    *,
    executable: str | None = None,
) -> ExternalCheck:
    binary = _find(executable or "", ("qcli", "qcli.exe"))
    if not binary:
        return ExternalCheck(
            tool="qctools",
            status="unavailable",
            detail="qcli was not found on PATH",
        )
    video = Path(video_path).expanduser().resolve()
    output = Path(output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = output / "qctools-report.qctools.xml.gz"
    started = time.perf_counter()
    completed = _run([binary, "-i", str(video), "-s", "-o", str(report)], timeout=300)
    elapsed = round(time.perf_counter() - started, 3)
    if completed.returncode != 0 or not report.is_file():
        return ExternalCheck(
            tool="qctools",
            status="fail",
            executable=binary,
            elapsed_seconds=elapsed,
            detail=f"QCTools failed with exit code {completed.returncode}",
            evidence={"stderr": completed.stderr[-500:]},
        )
    summary = parse_qctools_report(report)
    status: ToolStatus = "pass" if summary.parsed and summary.frames else "warn"
    return ExternalCheck(
        tool="qctools",
        status=status,
        executable=binary,
        elapsed_seconds=elapsed,
        output_path=str(report),
        output_sha256=sha256_file(report),
        detail=summary.detail,
        evidence={"frames": summary.frames, "bytes": report.stat().st_size},
    )


def run_mediaconch(
    video_path: Path | str,
    output_dir: Path | str,
    *,
    executable: str | None = None,
) -> ExternalCheck:
    binary = _find(executable or "", ("MediaConch", "mediaconch", "MediaConch.exe"))
    if not binary:
        return ExternalCheck(
            tool="mediaconch",
            status="unavailable",
            detail="MediaConch was not found on PATH",
        )
    video = Path(video_path).expanduser().resolve()
    output = Path(output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    command = [binary, str(video)]
    completed = _run(command, timeout=120)
    elapsed = round(time.perf_counter() - started, 3)
    raw = output / "mediaconch-report.txt"
    raw.write_text(completed.stdout + completed.stderr, encoding="utf-8")
    status, detail = summarize_mediaconch(completed.stdout, completed.returncode)
    return ExternalCheck(
        tool="mediaconch",
        status=status,
        executable=binary,
        elapsed_seconds=elapsed,
        output_path=str(raw),
        output_sha256=sha256_file(raw),
        detail=detail,
        evidence={
            "returncode": completed.returncode,
        },
    )


def run_external_qa(
    video_path: Path | str,
    output_dir: Path | str,
    *,
    qcli: str | None = None,
    mediaconch: str | None = None,
) -> ExternalQAReport:
    video = Path(video_path).expanduser().resolve()
    if not video.is_file():
        raise FileNotFoundError(f"Video not found: {video}")
    checks = [
        run_qctools(video, output_dir, executable=qcli),
        run_mediaconch(video, output_dir, executable=mediaconch),
    ]
    statuses = {check.status for check in checks}
    if "fail" in statuses:
        overall: Literal["pass", "warn", "fail"] = "fail"
    elif statuses & {"warn", "unavailable"}:
        overall = "warn"
    else:
        overall = "pass"
    body = {
        "generated_at": utc_now(),
        "video_path": str(video),
        "video_sha256": sha256_file(video),
        "overall": overall,
        "checks": [check.model_dump(mode="json") for check in checks],
    }
    return ExternalQAReport(**body, manifest_hash=sha256_json(body))


def external_qa_payload(report: ExternalQAReport) -> dict[str, Any]:
    return report.model_dump(mode="json")
