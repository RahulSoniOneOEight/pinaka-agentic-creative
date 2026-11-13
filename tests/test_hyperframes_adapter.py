from __future__ import annotations

import re
from pathlib import Path

import pytest

from pinaka.adapters.hyperframes import (
    PINNED_VERSION,
    REPO_ROOT,
    HyperFramesAdapter,
    frame_difference,
    video_duration,
)
from pinaka.composition import build_composition, composition_duration

adapter = HyperFramesAdapter()
requires_hyperframes = pytest.mark.skipif(
    not adapter.installed(), reason="HyperFrames is not installed (npm ci)"
)


def test_pinned_hyperframes_version_and_integrity():
    assert adapter.package_version() == PINNED_VERSION
    integrity = adapter.lock_integrity()
    assert integrity and integrity.startswith("sha512-")
    if adapter.installed():
        assert adapter.version() == PINNED_VERSION


def test_license_notice_covers_vendored_code():
    notice = (REPO_ROOT / "NOTICE").read_text(encoding="utf-8")
    assert "HyperFrames" in notice
    assert "Apache License 2.0" in notice
    assert PINNED_VERSION in notice
    assert "ShortGPT" in notice
    assert "MIT License" in notice
    assert "Copyright (c) 2024 Ray Ventura" in notice


def test_layer_manifest_emits_track_indices():
    html = build_composition(
        title="Track order",
        width=270,
        height=480,
        shots=[
            {
                "stable_key": "shot-01",
                "beat": "establish",
                "duration_seconds": 2,
                "narrative": "A duck on a pond",
                "requirements": {"subject": {"action": "bob", "count": 1}},
            }
        ],
        layers={
            "shot-01": [
                {"role": "background", "mime": "image/png", "data": "AAAA", "z": 0},
                {"role": "character", "mime": "image/png", "data": "BBBB", "z": 10},
                {"role": "effect", "mime": "image/png", "data": "CCCC", "z": 20},
            ]
        },
    )
    assert re.findall(r'data-track-index="(\d+)"', html) == ["0", "10", "20"]
    assert 'data-hf-scene="shot-01"' in html
    assert 'data-composition-id="pinaka-main"' in html
    assert 'data-width="270"' in html
    assert 'data-height="480"' in html


@requires_hyperframes
def test_hyperframes_render_matches_legacy_duration(tmp_path: Path):
    project = tmp_path / "hyperframes"
    html = adapter.write_smoke_project(
        project, width=270, height=480, duration=2, fps=12
    )
    report = adapter.lint(html)
    assert report["ok"] is True
    assert report["errorCount"] == 0

    legacy = build_composition(
        title="Legacy contract",
        width=270,
        height=480,
        shots=[
            {
                "stable_key": "shot-01",
                "beat": "establish",
                "duration_seconds": 2,
                "narrative": "A duck on a pond",
                "requirements": {"subject": {"action": "bob", "count": 1}},
            }
        ],
    )
    declared = composition_duration(legacy)
    output = tmp_path / "render.mp4"
    result = adapter.render(project, output, fps=12)
    assert result["version"] == PINNED_VERSION
    assert video_duration(output) == pytest.approx(float(declared), abs=1 / 12)


@requires_hyperframes
def test_hyperframes_render_is_deterministic(tmp_path: Path):
    project = tmp_path / "hyperframes"
    adapter.write_smoke_project(project, width=270, height=480, duration=1, fps=12)
    first = tmp_path / "first.mp4"
    second = tmp_path / "second.mp4"
    adapter.render(project, first, fps=12)
    adapter.render(project, second, fps=12)
    assert video_duration(first) == pytest.approx(video_duration(second), abs=1 / 12)
    difference = frame_difference(first, second)
    assert difference["max_mean"] <= 2.5, difference
