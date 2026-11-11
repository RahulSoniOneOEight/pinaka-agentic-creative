from __future__ import annotations

import pytest

from pinaka.controller import CreativeController
from pinaka.enums import ApprovalStage, ProjectStatus
from pinaka.errors import InvalidTransitionError
from pinaka.judge import VisualJudgement, VisualScores
from pinaka.models import ApprovalRequest
from pinaka.review_policy import CampaignRisk, ReviewPolicy, decide_review

from .conftest import advance_to_sourcing, create_project, record_final_candidate


def _archived_project(controller: CreativeController):
    project, _, _ = advance_to_sourcing(controller)
    controller.transition_project(
        project.id, ProjectStatus.ANIMATIC_READY, reason="Animatic ready"
    )
    controller.approve(
        project.id,
        ApprovalRequest(
            stage=ApprovalStage.ANIMATIC,
            approved_by="reviewer",
            scope={"animatic": "v1"},
        ),
    )
    controller.transition_project(
        project.id, ProjectStatus.FINAL_CANDIDATE, reason="Final rendered"
    )
    candidate = record_final_candidate(controller, project.id)
    controller.approve(
        project.id,
        ApprovalRequest(
            stage=ApprovalStage.FINAL,
            approved_by="reviewer",
            scope={"candidate": "final-v1"},
            output_hash=candidate["content_hash"],
        ),
    )
    controller.archive_project(project.id)
    return project


def test_revision_requires_an_archived_parent(controller):
    parent = create_project(controller)

    with pytest.raises(InvalidTransitionError, match="archived or finally approved"):
        controller.create_revision(
            parent.id, name="Revision", objective="Correct the captions"
        )


def test_revision_links_the_child_to_the_parent(controller):
    parent = _archived_project(controller)

    child = controller.create_revision(
        parent.id,
        name="Captions v2",
        objective="Correct caption readability and re-approve",
    )

    assert child.parent_project_id == parent.id
    assert child.status == ProjectStatus.DRAFT
    assert controller.repository.list_child_projects(parent.id) == [child]


def test_revision_lineage_walks_to_the_original(controller):
    parent = _archived_project(controller)
    child = controller.create_revision(
        parent.id, name="Rev 1", objective="Fix captions"
    )

    lineage = controller.revision_lineage(child.id)

    assert lineage == [child.id, parent.id]
    assert controller.revision_lineage(parent.id) == [parent.id]


def test_record_scene_plan_stores_markdown_and_hash(controller):
    project = create_project(controller)
    markdown = "# SCENE_PLAN\n\n## Scene 1\nHook the founder.\n"

    recorded = controller.record_scene_plan(project.id, markdown)

    assert recorded["content_hash"]
    assert recorded["relative_path"].endswith(".md")
    latest = controller.scene_plan(project.id)
    assert latest["markdown"] == markdown
    assert latest["content_hash"] == recorded["content_hash"]


def test_scene_plan_is_absent_before_recording(controller):
    project = create_project(controller)

    assert controller.scene_plan(project.id) is None


def _clean_judgement() -> VisualJudgement:
    return VisualJudgement(
        model="test-vision",
        scores=VisualScores(
            tone=0.9, brand=0.9, composition=0.9, legibility=0.9, safety=0.95
        ),
        findings=(),
        confidence=0.95,
        verdict="accept",
        image_hash="a" * 64,
        prompt_hash="b" * 64,
        response_hash="c" * 64,
        judged_at="2026-11-11T00:00:00.000Z",
    )


def test_high_risk_campaign_forces_human_review_even_when_clean():
    result = decide_review(
        _clean_judgement(),
        ReviewPolicy(),
        risk=CampaignRisk(monetized=True),
    )

    assert result["decision"] == "human_review"
    assert any("monetized" in reason for reason in result["reasons"])


def test_low_risk_campaign_still_auto_accepts():
    result = decide_review(
        _clean_judgement(),
        ReviewPolicy(),
        risk=CampaignRisk(),
    )

    assert result["decision"] == "auto_accept"


def test_high_risk_override_can_be_disabled():
    result = decide_review(
        _clean_judgement(),
        ReviewPolicy(require_human_when_high_risk=False),
        risk=CampaignRisk(advertising=True),
    )

    assert result["decision"] == "auto_accept"


def test_controller_judge_uses_campaign_risk(controller):
    project = create_project(controller)
    artifact = controller.write_artifact(
        project.id,
        kind="preview",
        relative_path="previews/hero.png",
        content=b"image-bytes",
        operation={"type": "test"},
    )

    class FakeJudge:
        def judge(self, content, *, intent, mime):
            return _clean_judgement()

    result = controller.judge_asset(
        project.id,
        artifact["id"],
        model="test-vision",
        intent="explainer",
        risk=CampaignRisk(makes_factual_claims=True),
        judge=FakeJudge(),
    )

    assert result["review"]["decision"] == "human_review"
    assert any("factual claims" in reason for reason in result["review"]["reasons"])
