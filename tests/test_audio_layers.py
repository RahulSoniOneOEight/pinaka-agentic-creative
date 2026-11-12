from __future__ import annotations

import json
import subprocess
from decimal import Decimal

import pytest

from pinaka.adapters.encode import FFmpegEncodeAdapter
from pinaka.adapters.mux import FFmpegMuxAdapter
from pinaka.assets import AssetCriteria
from pinaka.enums import ArtifactKind, SlotAssetRole
from pinaka.errors import IntegrityError
from pinaka.kids import check_cue_limits
from pinaka.models import AudioCueRequest
from pinaka.preflight import run_preflight
from pinaka.publication import (
    CHANNELS,
    PublicationAuthorization,
    PublicationRequest,
    evaluate_publication,
)
from pinaka.qa import check_audio_layers

from .conftest import create_project

CC0 = "https://creativecommons.org/publicdomain/zero/1.0/"
BY = "https://creativecommons.org/licenses/by/4.0/"


def _rights(
    *,
    licence: str = "CC0 1.0",
    url: str = CC0,
    commercial: bool | None = True,
    modifications: bool | None = True,
    attribution_required: bool | None = False,
    attribution_text: str | None = None,
) -> dict:
    return {
        "license_name": licence,
        "license_code": "cc0" if url == CC0 else "by",
        "license_url": url,
        "terms_url": url,
        "terms_reviewed_at": "2026-11-01",
        "commercial_use_allowed": commercial,
        "modifications_allowed": modifications,
        "editorial_only": False,
        "attribution_required": attribution_required,
        "attribution_text": attribution_text,
        "origin_license_verified": True,
    }


def _audio_artifact(controller, project_id: str, name: str, **rights) -> dict:
    return controller.write_artifact(
        project_id,
        kind=ArtifactKind.AUDIO,
        relative_path=f"artifacts/audio/{name}.wav",
        content=f"AUDIO-{name}".encode(),
        operation={"type": "sourced_audio", "rights": _rights(**rights)},
    )


def _cue(artifact_id: str, **overrides) -> AudioCueRequest:
    payload = {
        "cue_key": "sfx-shot-1",
        "artifact_id": artifact_id,
        "role": "sfx",
        "start_ms": 1000,
        "duration_ms": 400,
        "gain_db": Decimal(-18),
        "decided_by": "reviewer@example.test",
    }
    payload.update(overrides)
    return AudioCueRequest(**payload)


def test_audio_cue_accepts_cc0_and_records_the_decision(controller):
    project = create_project(controller)
    artifact = _audio_artifact(controller, project.id, "whoosh")

    cue = controller.assign_audio_cue(project.id, _cue(artifact["id"]))

    assert cue["role"] == "sfx"
    assert cue["start_ms"] == 1000
    assert cue["verdict"] == "accepted"
    decision = json.loads(cue["decision_json"])
    assert decision["commercial_use_allowed"] is True
    assert decision["modifications_allowed"] is True
    assert controller.audio_coverage(project.id)["cue_count"] == 1


def test_audio_cue_rejects_a_noncommercial_licence(controller):
    project = create_project(controller)
    artifact = _audio_artifact(
        controller,
        project.id,
        "nc-track",
        licence="CC BY-NC 4.0",
        url=BY,
        commercial=False,
    )

    with pytest.raises(IntegrityError, match="commercial use"):
        controller.assign_audio_cue(project.id, _cue(artifact["id"]))


def test_audio_cue_rejects_a_no_derivatives_licence(controller):
    project = create_project(controller)
    artifact = _audio_artifact(
        controller,
        project.id,
        "nd-track",
        licence="CC BY-ND 4.0",
        url=BY,
        modifications=False,
    )

    with pytest.raises(IntegrityError, match="modification"):
        controller.assign_audio_cue(project.id, _cue(artifact["id"]))


def test_audio_cue_requires_recorded_rights(controller):
    project = create_project(controller)
    artifact = controller.write_artifact(
        project.id,
        kind=ArtifactKind.AUDIO,
        relative_path="artifacts/audio/no-rights.wav",
        content=b"AUDIO",
        operation={"type": "sourced_audio"},
    )

    with pytest.raises(IntegrityError, match="licence evidence"):
        controller.assign_audio_cue(project.id, _cue(artifact["id"]))


def test_audio_cue_cannot_run_past_the_film_end(controller):
    project = create_project(controller)
    artifact = _audio_artifact(controller, project.id, "late")

    with pytest.raises(IntegrityError, match="past the end"):
        controller.assign_audio_cue(
            project.id,
            _cue(artifact["id"], start_ms=24_900, duration_ms=500),
        )


def test_audio_cue_count_is_capped(controller):
    project = create_project(controller)
    artifact = _audio_artifact(controller, project.id, "pop")
    for index in range(12):
        controller.assign_audio_cue(
            project.id,
            _cue(
                artifact["id"],
                cue_key=f"sfx-{index}",
                start_ms=index * 350,
                duration_ms=200,
            ),
        )

    with pytest.raises(IntegrityError, match="at most 12"):
        controller.assign_audio_cue(
            project.id,
            _cue(artifact["id"], cue_key="sfx-13", start_ms=5000),
        )


def test_audio_layer_plan_hashes_the_bed_and_every_cue(controller):
    project = create_project(controller)
    bed = _audio_artifact(
        controller,
        project.id,
        "bed",
        licence="CC BY 4.0",
        url=BY,
        attribution_required=True,
        attribution_text='"Ukulele" by Ada / Openverse / CC BY 4.0',
    )
    cue = _audio_artifact(controller, project.id, "pop")
    controller.assign_audio_bed(
        project.id,
        artifact_id=bed["id"],
        role=SlotAssetRole.PRIMARY,
        criteria=AssetCriteria(),
        decided_by="reviewer@example.test",
    )
    controller.assign_audio_cue(project.id, _cue(cue["id"]))

    plan = controller._audio_layer_plan(project.id)

    assert plan["bed"]["artifact_id"] == bed["id"]
    assert plan["bed"]["attribution"].startswith('"Ukulele"')
    assert [item["cue_key"] for item in plan["cues"]] == ["sfx-shot-1"]
    assert len(plan["dependencies"]) == 2
    assert len(plan["plan_hash"]) == 64
    assert plan["summary"]["plan_hash"] == plan["plan_hash"]
    assert plan["summary"]["bed"]["content_hash"] == bed["content_hash"]


def _manifest(**overrides) -> dict:
    layers = {
        "plan_hash": "a" * 64,
        "bed": {
            "artifact_id": "art_bed",
            "relative_path": "artifacts/audio/bed.wav",
            "content_hash": "b" * 64,
            "gain_db": -20.0,
            "licence": "CC0 1.0",
            "attribution": None,
            "attribution_required": False,
        },
        "cues": [
            {
                "cue_key": "sfx-1",
                "artifact_id": "art_sfx",
                "relative_path": "artifacts/audio/pop.wav",
                "content_hash": "c" * 64,
                "role": "sfx",
                "shot_id": None,
                "start_ms": 1000,
                "duration_ms": 400,
                "gain_db": -18.0,
                "loop": False,
                "licence": "CC0 1.0",
                "attribution": None,
                "attribution_required": False,
            }
        ],
    }
    layers.update(overrides)
    return {"audio_layers": layers}


def test_audio_layer_qa_passes_a_clean_plan():
    checks = {check.check: check for check in check_audio_layers(_manifest())}

    assert checks["audio.layers"].status == "pass"
    assert checks["audio.level"].status == "pass"
    assert checks["audio.density"].status == "pass"
    assert checks["audio.credits"].status == "pass"


def test_audio_layer_qa_rejects_an_over_loud_cue_and_stacking():
    layers = _manifest()["audio_layers"]
    layers["cues"] = [
        {**layers["cues"][0], "cue_key": "sfx-1", "gain_db": -2.0},
        {**layers["cues"][0], "cue_key": "sfx-2", "start_ms": 1500},
    ]
    checks = {
        check.check: check for check in check_audio_layers({"audio_layers": layers})
    }

    assert checks["audio.level"].status == "fail"
    assert checks["audio.density"].status == "fail"


def test_audio_layer_qa_requires_attribution_when_the_licence_does():
    layers = _manifest()["audio_layers"]
    layers["cues"][0]["attribution_required"] = True
    layers["cues"][0]["attribution"] = None

    checks = {
        check.check: check for check in check_audio_layers({"audio_layers": layers})
    }

    assert checks["audio.credits"].status == "fail"


def test_preflight_flags_an_unlicensed_cue(controller):
    project = create_project(controller)
    artifact = _audio_artifact(controller, project.id, "bad")
    controller.repository.upsert_audio_cue(
        project.id,
        cue_key="bad-cue",
        artifact_id=artifact["id"],
        role="sfx",
        start_ms=1000,
        duration_ms=400,
        gain_db="-18",
        loop=False,
        shot_id=None,
        verdict="accepted",
        decision={
            "commercial_use_allowed": False,
            "modifications_allowed": True,
            "attribution_required": False,
            "attribution_text": None,
        },
    )

    report = run_preflight(controller, project.id)
    rule = next(item for item in report["rules"] if item["rule_id"] == "R-AUDIO-001")

    assert rule["status"] == "fail"
    assert "commercial use not allowed" in rule["detail"]


def _publication_request(disclosures: tuple[str, ...]) -> PublicationRequest:
    return PublicationRequest(
        channel=next(iter(CHANNELS)),
        artifact_id="art_final",
        title="Pilot",
        disclosures=disclosures,
    )


def _authorization(request: PublicationRequest) -> PublicationAuthorization:
    return PublicationAuthorization(
        authorized=True,
        authorized_by="reviewer@example.test",
        artifact_hash="a" * 64,
        channels=(request.channel,),
    )


def _media_check(request: PublicationRequest, audio_layers: int):
    plan = evaluate_publication(
        project_id="prj_test",
        project_status="archived",
        request=request,
        authorization=_authorization(request),
        artifact_hash="a" * 64,
        final_approval_hash="a" * 64,
        duration_seconds=10.0,
        width_px=1080,
        height_px=1920,
        has_embedded_captions=True,
        caption_hash="b" * 64,
        audio_layers=audio_layers,
    )
    return next(check for check in plan.checks if check.rule_id == "P-MUSIC-001")


def test_publication_requires_a_music_licence_disclosure():
    request = _publication_request(())
    assert _media_check(request, audio_layers=2).status == "fail"

    disclosed = _publication_request(("music_licence",))
    assert _media_check(disclosed, audio_layers=2).status == "pass"


def test_audio_credits_records_every_layer(controller):
    project = create_project(controller)
    bed = _audio_artifact(
        controller,
        project.id,
        "bed-credits",
        licence="CC BY 4.0",
        url=BY,
        attribution_required=True,
        attribution_text='"Ukulele" by Ada / Openverse / CC BY 4.0',
    )
    cue = _audio_artifact(controller, project.id, "pop-credits")
    controller.assign_audio_bed(
        project.id,
        artifact_id=bed["id"],
        role=SlotAssetRole.PRIMARY,
        criteria=AssetCriteria(),
        decided_by="reviewer@example.test",
    )
    controller.assign_audio_cue(project.id, _cue(cue["id"]))

    result = controller.audio_credits(project.id)

    kinds = [entry["kind"] for entry in result["credits"]]
    assert kinds == ["music_bed", "audio_cue"]
    assert result["credits"][0]["attribution"].startswith('"Ukulele"')
    assert result["missing_attribution"] == []
    artifact = result["artifact"]
    assert artifact["relative_path"].startswith("artifacts/audio/credits-")
    assert controller.verify_artifact(artifact["id"])
    assert len(artifact["dependencies"]) == 2


def test_kids_cue_limits_reject_loud_or_dense_cues():
    checks = check_cue_limits(
        [
            {"cue_key": "a", "start_ms": 1000, "gain_db": -6.0},
            {"cue_key": "b", "start_ms": 1500, "gain_db": -18.0},
        ]
    )

    assert checks.status == "fail"
    assert "above" in checks.detail
    assert "closer" in checks.detail


def test_ffmpeg_mix_carries_the_bed_and_a_cue(tmp_path):
    executable = FFmpegEncodeAdapter.find_executable()

    def run(*args: str) -> None:
        subprocess.run(
            [executable, "-y", "-hide_banner", "-loglevel", "error", *args],
            check=True,
            timeout=120,
        )

    video = tmp_path / "silent.mp4"
    run(
        "-f",
        "lavfi",
        "-i",
        "color=c=black:s=320x568:r=10:d=3",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(video),
    )

    def tone(name: str, frequency: int, seconds: float):
        path = tmp_path / name
        run(
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={frequency}:duration={seconds}",
            "-ar",
            "48000",
            "-ac",
            "1",
            str(path),
        )
        return path

    narration = tone("narration.wav", 440, 3)
    bed = tone("bed.wav", 220, 6)
    cue = tone("cue.wav", 880, 0.5)

    adapter = FFmpegMuxAdapter(executable=executable)
    result = adapter.mux(
        video_path=video,
        audio_path=narration,
        duration_seconds=3.0,
        bed_path=bed,
        cues=[
            {
                "path": str(cue),
                "start_ms": 1000,
                "duration_ms": 500,
                "gain_db": -18,
                "loop": False,
            }
        ],
    )

    assert result.metadata["audio_codec"] == "aac"
    assert abs(result.metadata["duration_seconds"] - 3.0) <= 0.2
    assert result.content
    assert result.loudness["input_i"] is not None
