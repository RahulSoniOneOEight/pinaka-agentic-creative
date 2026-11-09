from pinaka.enums import ApprovalStage, ArtifactKind, ArtifactStatus, ProjectStatus
from pinaka.models import ApprovalRequest, DependencyRef, FactSetCreate

from .conftest import advance_to_sourcing


def test_fact_change_invalidates_only_dependent_work(controller):
    project, fact_v1, sourcing_approval = advance_to_sourcing(controller)
    controller.transition_project(
        project.id, ProjectStatus.ANIMATIC_READY, reason="Animatic ready for review"
    )
    animatic_approval = controller.approve(
        project.id,
        ApprovalRequest(
            stage=ApprovalStage.ANIMATIC,
            approved_by="producer@example.test",
            scope={"animatic": "v1"},
        ),
    )
    dependent = controller.write_artifact(
        project.id,
        kind=ArtifactKind.PREVIEW,
        relative_path="previews/price-card-v1.txt",
        content=b"Price: USD 9.99",
        operation={"type": "layout_preview"},
        dependencies=(
            DependencyRef(
                entity_type="fact_set",
                entity_id=fact_v1["id"],
                entity_hash=fact_v1["content_hash"],
            ),
        ),
    )
    unrelated = controller.write_artifact(
        project.id,
        kind=ArtifactKind.PREVIEW,
        relative_path="previews/generic-background.txt",
        content=b"Generic approved background",
        operation={"type": "preview"},
    )

    fact_v2 = controller.add_fact_set(
        project.id,
        FactSetCreate(
            source_ref="approved-fact-sheet-v2",
            values={"sku": "SKU-001", "price": "8.99", "currency": "USD"},
            evidence_refs=("test://facts/v2",),
        ),
    )

    artifacts = {
        item["id"]: item for item in controller.repository.list_artifacts(project.id)
    }
    events = controller.repository.events(project.id)

    assert fact_v2["version"] == 2
    assert (
        controller.repository.active_approval(project.id, ApprovalStage.BRIEF.value)
        is not None
    )
    assert (
        controller.repository.active_approval(project.id, ApprovalStage.SOURCING.value)
        is None
    )
    assert (
        controller.repository.active_approval(project.id, ApprovalStage.ANIMATIC.value)
        is None
    )
    assert artifacts[dependent["id"]]["status"] == ArtifactStatus.STALE.value
    assert artifacts[unrelated["id"]]["status"] == ArtifactStatus.CURRENT.value
    assert (
        controller.repository.get_project(project.id).status
        == ProjectStatus.FACTS_LOCKED
    )
    invalidated_ids = {
        event["entity_id"]
        for event in events
        if event["event_type"] == "approval.invalidated"
    }
    assert sourcing_approval["id"] in invalidated_ids
    assert animatic_approval["id"] in invalidated_ids
