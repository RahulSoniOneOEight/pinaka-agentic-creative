from __future__ import annotations

import pytest

from pinaka.enums import ArtifactKind
from pinaka.errors import ConfigurationError
from pinaka.judge import (
    OpenAICompatibleJudge,
    VisualFinding,
    VisualJudgement,
    VisualScores,
)
from pinaka.review_policy import ReviewDecision, ReviewPolicy, decide_review
from pinaka.utils import sha256_bytes

from .conftest import create_project


def judgement(
    *,
    scores: dict[str, float] | None = None,
    findings: tuple[VisualFinding, ...] = (),
    confidence: float = 0.95,
    verdict: str = "accept",
) -> VisualJudgement:
    values = {
        "tone": 0.9,
        "brand": 0.9,
        "composition": 0.9,
        "legibility": 0.9,
        "safety": 0.95,
    }
    values.update(scores or {})
    return VisualJudgement(
        model="test-vision",
        scores=VisualScores(**values),
        findings=findings,
        confidence=confidence,
        verdict=verdict,
        image_hash="a" * 64,
        prompt_hash="b" * 64,
        response_hash="c" * 64,
        judged_at="2026-11-11T00:00:00.000Z",
    )


def test_clean_judgement_is_auto_accepted():
    result = decide_review(judgement(), ReviewPolicy())

    assert result["decision"] == ReviewDecision.AUTO_ACCEPT.value


def test_blocking_finding_rejects():
    finding = VisualFinding(category="safety", severity="blocking", detail="unsafe")
    result = decide_review(judgement(findings=(finding,)), ReviewPolicy())

    assert result["decision"] == ReviewDecision.REJECT.value


def test_warning_routes_to_human_review():
    finding = VisualFinding(
        category="composition", severity="warning", detail="subject too small"
    )
    result = decide_review(judgement(findings=(finding,)), ReviewPolicy())

    assert result["decision"] == ReviewDecision.HUMAN_REVIEW.value


def test_low_confidence_routes_to_human_review():
    result = decide_review(judgement(confidence=0.4), ReviewPolicy())

    assert result["decision"] == ReviewDecision.HUMAN_REVIEW.value
    assert any("confidence" in reason for reason in result["reasons"])


def test_low_score_routes_to_human_review():
    result = decide_review(judgement(scores={"brand": 0.2}), ReviewPolicy())

    assert result["decision"] == ReviewDecision.HUMAN_REVIEW.value
    assert any("brand" in reason for reason in result["reasons"])


def test_revise_verdict_routes_to_human_review():
    result = decide_review(judgement(verdict="revise"), ReviewPolicy())

    assert result["decision"] == ReviewDecision.HUMAN_REVIEW.value


def test_failed_technical_checks_route_to_human_review():
    result = decide_review(judgement(), ReviewPolicy(), technical_ok=False)

    assert result["decision"] == ReviewDecision.HUMAN_REVIEW.value


def test_auto_accept_can_be_disabled():
    result = decide_review(judgement(), ReviewPolicy(allow_auto_accept=False))

    assert result["decision"] == ReviewDecision.HUMAN_REVIEW.value


def test_judge_requires_a_vision_credential(monkeypatch, tmp_path):
    monkeypatch.delenv("PINAKA_VISION_API_KEY", raising=False)
    monkeypatch.delenv("PINAKA_VISION_API_KEY_FILE", raising=False)
    monkeypatch.setattr("pinaka.judge.DEFAULT_API_KEY_FILE", tmp_path / "missing-key")

    with pytest.raises(ConfigurationError, match="vision credential"):
        OpenAICompatibleJudge(model="test-vision", api_key="").judge(
            b"image", intent="test"
        )


def test_judge_parses_a_compatible_response():
    payload = {
        "choices": [
            {
                "message": {
                    "content": (
                        '{"scores": {"tone": 0.8, "brand": 0.9, "composition": 0.7,'
                        ' "legibility": 0.9, "safety": 1.0}, "findings": [],'
                        ' "confidence": 0.9, "verdict": "accept"}'
                    )
                }
            }
        ]
    }
    import json

    body = json.dumps(payload).encode("utf-8")
    captured: dict[str, object] = {}

    def transport(url, headers, data):
        captured["url"] = url
        captured["headers"] = headers
        return body

    judge = OpenAICompatibleJudge(
        model="test-vision", api_key="secret", transport=transport
    )
    result = judge.judge(b"image-bytes", intent="brand explainer")

    assert result.model == "test-vision"
    assert result.verdict == "accept"
    assert result.image_hash == sha256_bytes(b"image-bytes")
    assert captured["headers"]["Authorization"] == "Bearer secret"


def test_controller_judge_records_a_qa_report(controller):
    project = create_project(controller)
    artifact = controller.write_artifact(
        project.id,
        kind=ArtifactKind.PREVIEW,
        relative_path="previews/hero.png",
        content=b"image-bytes",
        operation={"type": "test"},
    )

    class FakeJudge:
        def judge(self, content, *, intent, mime):
            return judgement()

    result = controller.judge_asset(
        project.id,
        artifact["id"],
        model="test-vision",
        intent="explainer",
        judge=FakeJudge(),
    )

    assert result["review"]["decision"] == ReviewDecision.AUTO_ACCEPT.value
    reports = [
        item
        for item in controller.repository.list_artifacts(project.id)
        if item["kind"] == ArtifactKind.QA.value
        and item["operation"].get("type") == "visual_judgement"
    ]
    assert len(reports) == 1
