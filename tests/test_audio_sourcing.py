from __future__ import annotations

from pinaka.analysis import AssetAnalyzer
from pinaka.assets import AssetCriteria
from pinaka.enums import (
    ArtifactKind,
    AssetAcquisitionMethod,
    AssetMediaType,
    AssetProvider,
    AssetUseClass,
    SlotAssetRole,
)
from pinaka.models import (
    AssetCandidate,
    AssetRightsRecord,
    AssetSourceRecord,
    AssetUseContext,
    ProviderComplianceRecord,
)

from .conftest import create_project

AUDIO_TYPE = "audio/mpeg"


def context() -> AssetUseContext:
    return AssetUseContext(
        use_class=AssetUseClass.COMMERCIAL,
        monetized=True,
        modification_planned=True,
        attribution_can_be_delivered=True,
    )


def audio_candidate(asset_id: str = "cc0-ukulele") -> AssetCandidate:
    return AssetCandidate(
        source=AssetSourceRecord(
            provider=AssetProvider.OPENVERSE,
            provider_asset_id=asset_id,
            media_type=AssetMediaType.AUDIO,
            acquisition_method=AssetAcquisitionMethod.OFFICIAL_API,
            source_page_url="https://example.test/origin",
            creator_name="Test Creator",
            direct_asset_url="https://example.test/track.mp3",
            api_response_hash="a" * 64,
        ),
        rights=AssetRightsRecord(
            license_name="CC0",
            license_code="cc0",
            license_url="https://creativecommons.org/publicdomain/zero/1.0/",
            terms_url="https://example.test/terms",
            terms_reviewed_at="2026-11-09",
            commercial_use_allowed=True,
            modifications_allowed=True,
            editorial_only=False,
            attribution_required=False,
            origin_license_verified=True,
            rights_uncertainty=(),
        ),
        compliance=ProviderComplianceRecord(
            human_selected=True, openverse_disclosure_planned=True
        ),
    )


def test_audio_download_registers_an_original(controller):
    project = create_project(controller)
    candidate = audio_candidate()
    captured = controller.capture_asset_evidence(
        project.id,
        candidate,
        fetcher=lambda url: (b"<html>licence</html>", "text/html"),
    )

    result = controller.download_stock_asset(
        project.id,
        candidate=captured["candidate"],
        context=context(),
        fetcher=lambda url: (b"ID3fake-audio-bytes", AUDIO_TYPE),
    )

    artifact = result["artifact"]
    assert artifact["kind"] == ArtifactKind.ORIGINAL.value
    assert artifact["relative_path"].endswith(".mp3")
    assert result["content_type"] == AUDIO_TYPE


def test_analyzer_reads_audio_without_extracting_a_frame(tmp_path):
    asset = tmp_path / "track.mp3"
    asset.write_bytes(b"ID3")

    def probe(path):
        return {
            "format": {"duration": "10.0"},
            "streams": [
                {"codec_type": "audio", "codec_name": "mp3", "channels": 2},
            ],
        }

    def forbidden_frame(path):
        raise AssertionError("no frame should be extracted from audio")

    analyzer = AssetAnalyzer(probe=probe, frame=forbidden_frame)
    observation = analyzer.analyze(asset, required_duration_seconds=10.0)

    assert observation.media_type == AssetMediaType.AUDIO
    assert observation.width is None
    assert observation.duration_seconds == 10.0
    assert observation.proposed_criteria().motion_suitable is True


def test_primary_and_backup_music_beds(controller):
    project = create_project(controller)
    primary = controller.write_artifact(
        project.id,
        kind=ArtifactKind.AUDIO,
        relative_path="audio/bed-primary.mp3",
        content=b"primary",
        operation={"type": "test_audio"},
    )
    backup = controller.write_artifact(
        project.id,
        kind=ArtifactKind.AUDIO,
        relative_path="audio/bed-backup.mp3",
        content=b"backup",
        operation={"type": "test_audio"},
    )

    controller.assign_audio_bed(
        project.id,
        artifact_id=primary["id"],
        role=SlotAssetRole.PRIMARY,
        criteria=AssetCriteria(),
        context=context(),
        decided_by="reviewer@example.test",
    )
    controller.assign_audio_bed(
        project.id,
        artifact_id=backup["id"],
        role=SlotAssetRole.BACKUP,
        criteria=AssetCriteria(),
        context=context(),
        decided_by="reviewer@example.test",
    )

    coverage = controller.audio_coverage(project.id)
    assert coverage["resolved"] is True
    assert coverage["primary_artifact_id"] == primary["id"]
    assert coverage["backup_artifact_id"] == backup["id"]


def test_music_without_backup_is_incomplete(controller):
    project = create_project(controller)
    primary = controller.write_artifact(
        project.id,
        kind=ArtifactKind.AUDIO,
        relative_path="audio/only.mp3",
        content=b"only",
        operation={"type": "test_audio"},
    )
    controller.assign_audio_bed(
        project.id,
        artifact_id=primary["id"],
        role=SlotAssetRole.PRIMARY,
        criteria=AssetCriteria(),
        context=context(),
        decided_by="reviewer@example.test",
    )

    coverage = controller.audio_coverage(project.id)
    assert coverage["resolved"] is False
    assert coverage["problems"] == ["missing backup"]
