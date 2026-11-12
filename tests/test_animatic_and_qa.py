import json
from pathlib import Path

import pytest

from pinaka.adapters.encode import EncodedVideo
from pinaka.adapters.frames import CapturedFrame, FrameCaptureResult, frame_time
from pinaka.adapters.mux import MuxedVideo
from pinaka.adapters.narration import SynthesizedNarration
from pinaka.animatic import render_html, render_srt, text_fragments
from pinaka.capabilities import probe_all
from pinaka.enums import ApprovalStage, ProjectStatus
from pinaka.errors import IntegrityError
from pinaka.models import ApprovalRequest, PilotContract
from pinaka.qa import contrast_ratio, run_animatic_qa
from pinaka.utils import sha256_bytes, sha256_file, sha256_json

PILOT_CONTRACT = (
    Path(__file__).parents[1] / "config" / "pilots" / "pinaka-virtual-cfo-10s.json"
)


def load_pilot_contract() -> PilotContract:
    return PilotContract.model_validate_json(PILOT_CONTRACT.read_text(encoding="utf-8"))


def prepare_animatic_project(controller):
    contract = load_pilot_contract()
    project, _ = controller.create_project_from_contract(contract)
    controller.apply_pilot_plan(project.id, contract)
    controller.approve(
        project.id,
        ApprovalRequest(
            stage=ApprovalStage.BRIEF,
            approved_by="rahul-soni",
            scope={"pilot": "virtual-cfo-10s", "paid_calls": False},
        ),
    )
    controller.ground_pilot_from_contract(project.id, contract)
    controller.transition_project(
        project.id,
        ProjectStatus.SOURCING,
        reason="Begin governed zero-cost sourcing",
    )
    controller.approve(
        project.id,
        ApprovalRequest(
            stage=ApprovalStage.SOURCING,
            approved_by="rahul-soni",
            scope={"sources": ["authorized-client-site"], "paid_calls": False},
        ),
    )
    return project


class FakeFrameCapture:
    profile_key = "chrome-headless-capture"

    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.calls = 0

    def capture(
        self, *, html_path, frame_count, fps, width, height
    ) -> FrameCaptureResult:
        if self.fail:
            raise RuntimeError("Fixture frame-capture failure")
        self.calls += 1
        frames = []
        for index in range(frame_count):
            content = (
                b"\x89PNG\r\n\x1a\n"
                + f"fixture-{index}-{frame_time(index, fps)}".encode()
            )
            frames.append(
                CapturedFrame(
                    index=index,
                    time_seconds=str(frame_time(index, fps)),
                    content=content,
                    content_hash=sha256_bytes(content),
                )
            )
        return FrameCaptureResult(
            frames=tuple(frames),
            renderer_version="FakeChrome/1.0",
            command_hash=sha256_json(
                {
                    "frame_count": frame_count,
                    "fps": fps,
                    "width": width,
                    "height": height,
                }
            ),
        )


class FakeVideoEncode:
    profile_key = "ffmpeg-h264"

    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.calls = 0

    def encode(self, *, frames_dir, frame_count, fps, width, height) -> EncodedVideo:
        if self.fail:
            raise RuntimeError("Fixture encode failure")
        self.calls += 1
        content = b"\x00\x00\x00\x18ftypmp42" + b"fixture-video" * 1000
        return EncodedVideo(
            content=content,
            content_hash=sha256_bytes(content),
            metadata={
                "format": "mov,mp4,m4a,3gp,3g2,mj2",
                "duration_seconds": 10.0,
                "codec": "h264",
                "width_px": width,
                "height_px": height,
                "fps": float(fps),
                "has_audio": False,
                "size_bytes": len(content),
            },
            command_hash=sha256_json(
                {
                    "frame_count": frame_count,
                    "fps": fps,
                    "width": width,
                    "height": height,
                }
            ),
            encoder_version="FakeFFmpeg/1.0",
        )


class FakeNarration:
    profile_key = "kokoro-tts"

    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.calls = 0

    def synthesize(self, text: str) -> SynthesizedNarration:
        if self.fail:
            raise RuntimeError("Fixture narration failure")
        self.calls += 1
        content = b"RIFF" + b"fixture-wav" * 200
        return SynthesizedNarration(
            content=content,
            content_hash=sha256_bytes(content),
            text_hash=sha256_bytes(text.encode()),
            voice="af_heart",
            speed=1.0,
            sample_rate=24000,
            duration_seconds=9.45,
            model_name="FakeKokoro/1.0",
            model_sha256="a" * 64,
            voices_sha256="b" * 64,
            licence_note="Fixture narration for tests only",
            command_hash=sha256_json({"text": text, "voice": "af_heart", "speed": 1.0}),
        )


class FakeMux:
    profile_key = "ffmpeg-mux"

    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.calls = 0

    def mux(self, *, video_path, audio_path, duration_seconds) -> MuxedVideo:
        if self.fail:
            raise RuntimeError("Fixture mux failure")
        self.calls += 1
        content = b"\x00\x00\x00\x18ftypmp42" + b"voiced" * 1000
        return MuxedVideo(
            content=content,
            content_hash=sha256_bytes(content),
            metadata={
                "duration_seconds": 10.0,
                "video_codec": "h264",
                "width_px": 1080,
                "height_px": 1920,
                "fps": 30.0,
                "audio_codec": "aac",
                "audio_sample_rate": 48000,
                "audio_channels": "stereo",
                "size_bytes": len(content),
            },
            loudness={
                "input_i": "-16.0",
                "input_tp": "-1.5",
                "input_lra": "2.0",
                "input_thresh": "-26.0",
                "target_offset": "0.0",
            },
            command_hash=sha256_json(
                {
                    "video": str(video_path),
                    "audio": str(audio_path),
                    "duration": duration_seconds,
                }
            ),
            encoder_version="FakeFFmpeg/1.0",
        )


def test_animatic_bundle_is_deterministic_and_zero_cost(controller):
    project = prepare_animatic_project(controller)
    result = controller.produce_animatic(project.id)

    assert result["resumed"] is False
    assert result["project"]["status"] == ProjectStatus.ANIMATIC_READY.value
    artifacts = result["artifacts"]
    assert set(artifacts) == {"timeline", "animatic", "captions"}
    for artifact in artifacts.values():
        assert controller.verify_artifact(artifact["id"])
        assert artifact["content_hash"]

    timeline = json.loads(controller.read_artifact_text(artifacts["timeline"]["id"]))
    html_text = controller.read_artifact_text(artifacts["animatic"]["id"])
    srt_text = controller.read_artifact_text(artifacts["captions"]["id"])

    assert timeline["total_duration_seconds"] == "10"
    assert [segment["stable_key"] for segment in timeline["segments"]] == [
        "problem-hook",
        "service-value",
        "decision-clarity",
        "brand-cta",
    ]
    assert timeline["timeline_hash"]
    assert "Growing business." in html_text
    assert "Unclear cash flow?" in html_text
    assert "Forecasts" in html_text
    assert "Budgets" in html_text
    assert "Financial insight" in html_text
    assert "Decide with clarity" in html_text
    assert "Start a Conversation" in html_text
    assert "pinakaintelligence.com" in html_text
    assert "Guaranteed profit" not in html_text
    assert "00:00:00,000 --> 00:00:02,000" in srt_text
    assert "00:00:10,000" in srt_text

    operations = controller.repository.list_tool_operations(project.id)
    assert len(operations) == 3
    assert {operation["status"] for operation in operations} == {"succeeded"}
    for operation in operations:
        receipt = controller.repository.get_execution_receipt(operation["id"])
        assert receipt is not None
        assert receipt["actual_cost_minor"] == 0

    repeated = controller.produce_animatic(project.id)
    assert repeated["resumed"] is True
    assert repeated["artifacts"]["animatic"]["id"] == artifacts["animatic"]["id"]


def test_animatic_qa_records_pass_and_is_idempotent(controller):
    project = prepare_animatic_project(controller)
    controller.produce_animatic(project.id)

    qa = controller.qa_animatic(project.id)
    assert qa["overall"] == "pass"
    assert len(qa["checks"]) >= 10
    assert controller.verify_artifact(qa["report_artifact"]["id"])
    assert {row["status"] for row in qa["qa_results"]} == {"pass"}
    names = {row["check_name"] for row in qa["qa_results"]}
    assert {
        "timeline.duration",
        "timeline.sequence",
        "content.exact_text",
        "content.prohibited_claims",
        "brand.colors",
        "typography.scale",
        "a11y.contrast",
        "spacing.grid",
        "captions.coverage",
        "rights.grounding",
        "artifacts.hashes",
    } == names

    snapshot = controller.status_snapshot(project.id)
    assert len(snapshot["qa_results"]) == len(qa["qa_results"])

    repeated = controller.qa_animatic(project.id)
    assert repeated["overall"] == "pass"
    assert len(controller.repository.list_qa_results(project.id)) == len(
        qa["qa_results"]
    )


def test_qa_detects_prohibited_copy_and_low_contrast(controller):
    project = prepare_animatic_project(controller)
    result = controller.produce_animatic(project.id)
    timeline = result["timeline"]

    tampered = json.loads(json.dumps(timeline))
    tampered["prohibited_claims"] = ["Guaranteed profit"]
    html_text = render_html(tampered).decode("utf-8") + "<p>Guaranteed profit</p>"
    srt_text = render_srt(tampered).decode("utf-8")
    checks = run_animatic_qa(
        timeline=tampered,
        html_text=html_text,
        srt_text=srt_text,
        artifacts_verified=True,
        grounding_kinds={"brand", "claims", "sources"},
    )
    by_name = {check.check: check for check in checks}
    assert by_name["content.prohibited_claims"].status.value == "fail"
    assert by_name["content.exact_text"].status.value == "pass"

    assert round(contrast_ratio("#FFFFFF", "#FFFFFF"), 2) == 1.0
    failed_contrast = json.loads(json.dumps(timeline))
    failed_contrast["brand"]["palette"]["blue"] = "#FFFFFF"
    checks = run_animatic_qa(
        timeline=failed_contrast,
        html_text=render_html(failed_contrast).decode("utf-8"),
        srt_text=render_srt(failed_contrast).decode("utf-8"),
        artifacts_verified=True,
        grounding_kinds={"brand", "claims", "sources"},
    )
    assert {check.check: check.status.value for check in checks}[
        "a11y.contrast"
    ] == "fail"


def test_text_fragments_preserve_approved_punctuation():
    assert text_fragments("Growing business. Unclear cash flow?") == [
        "Growing business.",
        "Unclear cash flow?",
    ]
    assert text_fragments("Forecasts \u00b7 Budgets \u00b7 Financial insight") == [
        "Forecasts",
        "Budgets",
        "Financial insight",
    ]


def test_capability_probe_reports_media_and_model_tooling():
    results = probe_all()
    names = {result.name for result in results}
    assert len(names) == len(results)
    assert {
        "ffmpeg",
        "ffprobe",
        "mediainfo",
        "tesseract",
        "playwright",
        "openai",
        "deepseek",
        "fal",
    }.issubset(names)
    assert all(result.checked_at for result in results)


def test_frame_capture_records_manifest_and_is_idempotent(controller):
    project = prepare_animatic_project(controller)
    controller.produce_animatic(project.id)
    capture = FakeFrameCapture()

    result = controller.capture_animatic_frames(
        project.id, frame_count=10, fps=1, adapter=capture
    )

    assert result["resumed"] is False
    manifest = result["frame_manifest"]
    assert manifest["frame_count"] == 10
    assert manifest["fps"] == 1
    assert manifest["duration_seconds"] == "10"
    assert manifest["width_px"] == 1080
    assert manifest["height_px"] == 1920
    assert len(manifest["frames"]) == 10
    for frame in manifest["frames"]:
        path = controller.artifact_store.resolve(project.id, frame["relative_path"])
        assert path.is_file()
        assert sha256_file(path) == frame["content_hash"]
    assert controller.verify_artifact(result["manifest_artifact"]["id"])
    assert result["receipt"]["status"] == "succeeded"
    assert result["receipt"]["actual_cost_minor"] == 0

    repeated = controller.capture_animatic_frames(
        project.id, frame_count=10, fps=1, adapter=capture
    )
    assert repeated["resumed"] is True
    assert repeated["manifest_artifact"]["id"] == result["manifest_artifact"]["id"]
    assert capture.calls == 1


def test_frame_capture_rejects_duration_mismatch_and_records_failure(controller):
    project = prepare_animatic_project(controller)
    controller.produce_animatic(project.id)

    with pytest.raises(IntegrityError):
        controller.capture_animatic_frames(
            project.id, frame_count=9, fps=1, adapter=FakeFrameCapture()
        )

    with pytest.raises(IntegrityError):
        controller.capture_animatic_frames(
            project.id, frame_count=10, fps=1, adapter=FakeFrameCapture(fail=True)
        )
    operations = controller.repository.list_tool_operations(project.id)
    capture_operations = [
        operation
        for operation in operations
        if operation["operation"] == "capture_frames"
    ]
    assert len(capture_operations) == 1
    receipt = controller.repository.get_execution_receipt(capture_operations[0]["id"])
    assert receipt is not None
    assert receipt["status"] == "failed"


def test_silent_encode_records_manifest_and_is_idempotent(controller):
    project = prepare_animatic_project(controller)
    controller.produce_animatic(project.id)
    controller.capture_animatic_frames(
        project.id, frame_count=10, fps=1, adapter=FakeFrameCapture()
    )
    encoder = FakeVideoEncode()

    result = controller.encode_silent_animatic(project.id, adapter=encoder)

    assert result["resumed"] is False
    assert result["project"]["status"] == ProjectStatus.ANIMATIC_READY.value
    video = result["video_artifact"]
    assert video["kind"] == "render"
    assert video["relative_path"].endswith(".mp4")
    assert controller.verify_artifact(video["id"])
    manifest = result["encode_manifest"]
    assert manifest["codec"] == "h264"
    assert manifest["audio"] is False
    assert manifest["frame_count"] == 10
    assert manifest["fps"] == 1
    assert manifest["duration_seconds"] == "10"
    assert manifest["video_hash"] == video["content_hash"]
    assert controller.verify_artifact(result["encode_manifest_artifact"]["id"])
    assert result["receipt"]["status"] == "succeeded"
    assert result["receipt"]["actual_cost_minor"] == 0

    repeated = controller.encode_silent_animatic(project.id, adapter=encoder)
    assert repeated["resumed"] is True
    assert repeated["video_artifact"]["id"] == video["id"]
    assert encoder.calls == 1


def test_silent_encode_failure_is_recorded(controller):
    project = prepare_animatic_project(controller)
    controller.produce_animatic(project.id)
    controller.capture_animatic_frames(
        project.id, frame_count=10, fps=1, adapter=FakeFrameCapture()
    )

    with pytest.raises(IntegrityError):
        controller.encode_silent_animatic(
            project.id, adapter=FakeVideoEncode(fail=True)
        )
    encode_operations = [
        operation
        for operation in controller.repository.list_tool_operations(project.id)
        if operation["operation"] == "encode_silent_master"
    ]
    assert len(encode_operations) == 1
    receipt = controller.repository.get_execution_receipt(encode_operations[0]["id"])
    assert receipt is not None
    assert receipt["status"] == "failed"


def test_narration_mux_and_voiced_qa(controller):
    project = prepare_animatic_project(controller)
    controller.produce_animatic(project.id)
    controller.capture_animatic_frames(
        project.id, frame_count=10, fps=1, adapter=FakeFrameCapture()
    )
    controller.encode_silent_animatic(project.id, adapter=FakeVideoEncode())
    narrator = FakeNarration()

    narration = controller.synthesize_narration(project.id, adapter=narrator)
    assert narration["resumed"] is False
    wav = narration["narration_artifact"]
    assert wav["kind"] == "audio"
    assert wav["relative_path"].endswith(".wav")
    assert controller.verify_artifact(wav["id"])
    assert narration["narration_manifest"]["duration_seconds"] == 9.45
    assert narration["narration_manifest"]["text_hash"]
    assert narration["receipt"]["status"] == "succeeded"
    assert narration["receipt"]["actual_cost_minor"] == 0

    mux = controller.mux_voiced_animatic(project.id, adapter=FakeMux())
    assert mux["resumed"] is False
    voiced = mux["video_artifact"]
    assert voiced["kind"] == "render"
    assert voiced["relative_path"].endswith(".mp4")
    assert controller.verify_artifact(voiced["id"])
    assert mux["mux_manifest"]["metadata"]["audio_codec"] == "aac"
    assert mux["mux_manifest"]["narration_voice"] == "af_heart"
    assert mux["receipt"]["status"] == "succeeded"

    qa = controller.qa_voiced_animatic(project.id)
    assert qa["overall"] == "pass"
    names = {check["check"] for check in qa["checks"]}
    assert {
        "render.duration",
        "render.video",
        "render.audio",
        "audio.loudness",
        "audio.peak",
        "narration.fit",
        "narration.script",
        "artifacts.hashes",
        "audio.layers",
    } == names
    assert {row["status"] for row in qa["qa_results"]} == {"pass"}
    assert controller.verify_artifact(qa["report_artifact"]["id"])
    assert qa["project"]["status"] == ProjectStatus.ANIMATIC_READY.value

    repeated = controller.synthesize_narration(project.id, adapter=narrator)
    assert repeated["resumed"] is True
    assert narrator.calls == 1
    repeated_mux = controller.mux_voiced_animatic(project.id, adapter=FakeMux())
    assert repeated_mux["resumed"] is True
    assert repeated_mux["video_artifact"]["id"] == voiced["id"]


def test_narration_and_mux_failures_are_recorded(controller):
    project = prepare_animatic_project(controller)
    controller.produce_animatic(project.id)
    controller.capture_animatic_frames(
        project.id, frame_count=10, fps=1, adapter=FakeFrameCapture()
    )
    controller.encode_silent_animatic(project.id, adapter=FakeVideoEncode())

    with pytest.raises(IntegrityError):
        controller.synthesize_narration(project.id, adapter=FakeNarration(fail=True))
    narration_operations = [
        operation
        for operation in controller.repository.list_tool_operations(project.id)
        if operation["operation"] == "synthesize_narration"
    ]
    assert len(narration_operations) == 1
    assert (
        controller.repository.get_execution_receipt(narration_operations[0]["id"])[
            "status"
        ]
        == "failed"
    )

    controller.synthesize_narration(
        project.id, voice="af_bella", adapter=FakeNarration()
    )
    with pytest.raises(IntegrityError):
        controller.mux_voiced_animatic(project.id, adapter=FakeMux(fail=True))
    mux_operations = [
        operation
        for operation in controller.repository.list_tool_operations(project.id)
        if operation["operation"] == "mux_narration"
    ]
    assert len(mux_operations) == 1
    assert (
        controller.repository.get_execution_receipt(mux_operations[0]["id"])["status"]
        == "failed"
    )
