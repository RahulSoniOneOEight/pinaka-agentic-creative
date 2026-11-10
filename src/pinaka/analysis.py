from __future__ import annotations

import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import Field

from .assets import AssetCriteria
from .enums import AssetMediaType
from .media_probe import probe_ffprobe
from .models import StrictModel
from .review import find_tesseract

SHARPNESS_FLOOR = 40.0
ASPECT_TOLERANCE = 0.02


class AssetObservation(StrictModel):
    """Deterministic measurements used to propose treatment criteria."""

    media_type: AssetMediaType
    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None
    aspect_ratio: float | None = None
    required_aspect_ratio: float | None = None
    required_width: int | None = None
    required_height: int | None = None
    required_duration_seconds: float | None = None
    text_detected: bool | None = None
    text_sample: str | None = None
    face_count: int | None = None
    sharpness: float | None = None
    notes: tuple[str, ...] = ()
    tools: dict[str, str | None] = Field(default_factory=dict)

    def proposed_criteria(self) -> AssetCriteria:
        notes: list[str] = []
        framing_ok = True
        if self.required_aspect_ratio and self.aspect_ratio:
            delta = abs(self.aspect_ratio - self.required_aspect_ratio)
            framing_ok = delta / self.required_aspect_ratio <= ASPECT_TOLERANCE
            if not framing_ok:
                notes.append(
                    f"Aspect {self.aspect_ratio:.4f} differs from "
                    f"{self.required_aspect_ratio:.4f}"
                )

        resolution_sufficient = True
        if self.required_width and self.width and self.width < self.required_width:
            resolution_sufficient = False
        if self.required_height and self.height and self.height < self.required_height:
            resolution_sufficient = False

        motion_suitable = True
        if (
            self.media_type == AssetMediaType.VIDEO
            and self.required_duration_seconds
            and self.duration_seconds is not None
        ):
            motion_suitable = self.duration_seconds >= self.required_duration_seconds

        sharp = self.sharpness is None or self.sharpness >= SHARPNESS_FLOOR
        if not sharp:
            notes.append(f"Sharpness {self.sharpness:.1f} is below {SHARPNESS_FLOOR}")

        return AssetCriteria(
            framing_ok=framing_ok,
            resolution_sufficient=resolution_sufficient,
            contains_conflicting_text=bool(self.text_detected),
            contains_recognizable_person_or_brand=bool(self.face_count),
            has_visible_artifacts=not sharp,
            motion_suitable=motion_suitable,
            notes=tuple(notes),
        )


def _extract_frame(path: Path, executable: str) -> Path:
    directory = Path(tempfile.mkdtemp(prefix="pinaka-analyse-"))
    target = directory / "frame.png"
    completed = subprocess.run(
        [
            executable,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-frames:v",
            "1",
            str(target),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if completed.returncode != 0 or not target.is_file():
        raise RuntimeError("Could not extract an analysis frame")
    return target


def _default_ocr(image: Path) -> str:
    tesseract = find_tesseract()
    completed = subprocess.run(
        [tesseract, str(image), "stdout", "--psm", "6"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    return completed.stdout.strip()


def _default_faces(image: Path) -> int | None:
    try:
        import cv2
    except ImportError:
        return None
    classifier_path = (
        Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
    )
    if not classifier_path.is_file():
        return None
    classifier = cv2.CascadeClassifier(str(classifier_path))
    frame = cv2.imread(str(image))
    if frame is None:
        return None
    grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    faces = classifier.detectMultiScale(grey, scaleFactor=1.1, minNeighbors=5)
    return len(faces)


def _default_sharpness(image: Path) -> float | None:
    try:
        import cv2
    except ImportError:
        return None
    frame = cv2.imread(str(image), cv2.IMREAD_GRAYSCALE)
    if frame is None:
        return None
    variance = cv2.Laplacian(frame, cv2.CV_64F).var()
    return round(float(variance), 3)


class AssetAnalyzer:
    """Measures an asset with local tools and proposes treatment criteria."""

    profile_key = "local-asset-analysis"

    def __init__(
        self,
        *,
        probe: Callable[[Path], dict[str, Any]] = probe_ffprobe,
        ocr: Callable[[Path], str] | None = None,
        faces: Callable[[Path], int | None] | None = None,
        sharpness: Callable[[Path], float | None] | None = None,
        frame: Callable[[Path], Path] | None = None,
        ffmpeg: str | None = None,
    ):
        self.probe = probe
        self.ocr = ocr
        self.faces = faces
        self.sharpness = sharpness
        self.frame = frame
        self.ffmpeg = ffmpeg

    def _ffmpeg(self) -> str:
        from .adapters.encode import FFmpegEncodeAdapter

        return self.ffmpeg or FFmpegEncodeAdapter.find_executable()

    def analyze(
        self,
        source: Path | str,
        *,
        required_width: int | None = None,
        required_height: int | None = None,
        required_duration_seconds: float | None = None,
    ) -> AssetObservation:
        path = Path(source).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Asset not found: {path}")
        probe = self.probe(path)
        streams = probe.get("streams") or []
        video = next(
            (item for item in streams if item.get("codec_type") == "video"), None
        )
        if not video:
            raise ValueError("Asset has no video stream")
        width = int(video.get("width") or 0) or None
        height = int(video.get("height") or 0) or None
        duration = probe.get("format", {}).get("duration")
        duration_seconds = float(duration) if duration else None
        media_type = (
            AssetMediaType.IMAGE
            if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".avif"}
            else AssetMediaType.VIDEO
        )
        aspect = round(width / height, 6) if width and height else None
        required_aspect = (
            round(required_width / required_height, 6)
            if required_width and required_height
            else None
        )

        text_sample: str | None = None
        text_detected: bool | None = None
        face_count: int | None = None
        sharpness: float | None = None
        tools: dict[str, str | None] = {"ffprobe": "available"}

        extract = self.frame or (lambda item: _extract_frame(item, self._ffmpeg()))

        if self.ocr or self.faces or self.sharpness:
            frame = extract(path)
            if self.ocr:
                text_sample = self.ocr(frame) or None
                text_detected = bool(text_sample)
                tools["tesseract"] = "available"
            if self.faces:
                face_count = self.faces(frame)
                tools["opencv_faces"] = "available" if face_count is not None else None
            if self.sharpness:
                sharpness = self.sharpness(frame)
                tools["opencv_sharpness"] = (
                    "available" if sharpness is not None else None
                )
        else:
            frame = extract(path)
            try:
                text_sample = _default_ocr(frame) or None
                text_detected = bool(text_sample)
                tools["tesseract"] = "available"
            except Exception:  # noqa: BLE001 - optional tool
                tools["tesseract"] = None
            face_count = _default_faces(frame)
            tools["opencv_faces"] = "available" if face_count is not None else None
            sharpness = _default_sharpness(frame)
            tools["opencv_sharpness"] = "available" if sharpness is not None else None

        return AssetObservation(
            media_type=media_type,
            width=width,
            height=height,
            duration_seconds=duration_seconds,
            aspect_ratio=aspect,
            required_aspect_ratio=required_aspect,
            required_width=required_width,
            required_height=required_height,
            required_duration_seconds=required_duration_seconds,
            text_detected=text_detected,
            text_sample=(text_sample[:500] if text_sample else None),
            face_count=face_count,
            sharpness=sharpness,
            tools=tools,
        )
