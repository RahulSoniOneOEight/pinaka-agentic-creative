from __future__ import annotations

from pinaka.creative_rules import CEILING_ORDER
from pinaka.recipes import RECIPES, build_plan, select_recipe

from .test_creative_rules import make_profile


def slot_roles(plan):
    return {slot.role: slot for slot in plan.slots}


def test_recipe_ids_are_unique_and_versioned():
    ids = [recipe.id for recipe in RECIPES]
    assert len(ids) == len(set(ids))
    assert all(recipe.version for recipe in RECIPES)
    assert {recipe.default_ceiling for recipe in RECIPES} <= set(CEILING_ORDER)


def test_hero_direct_sell_selects_offer_short():
    profile = make_profile(
        deliverable={
            "output_kind": "video",
            "duration_seconds": "15",
            "orientation": "vertical",
            "aspect_ratio": "9:16",
            "width_px": 1080,
            "height_px": 1920,
        },
        product_role="hero",
        form="direct",
        subjects=["food"],
        tones=["urgent"],
        primary_outcome="sell",
        asset_readiness="photos_and_footage",
    )
    selection = select_recipe(profile)
    assert selection.recipe.id == "offer-short"
    assert selection.runner_up is not None
    assert selection.score > 0


def test_money_explainer_selects_explainer_short():
    profile = make_profile(
        subjects=["money"],
        primary_outcome="inform",
        secondary_outcomes=["build_trust"],
    )
    selection = select_recipe(profile)
    assert selection.recipe.id == "explainer-short"
    assert "form" in selection.matched_dimensions
    assert selection.rationale


def test_pilot_profile_plan_is_bound_and_net_zero():
    profile = make_profile(
        deliverable={
            "output_kind": "video",
            "duration_seconds": "10",
            "orientation": "vertical",
            "aspect_ratio": "9:16",
            "width_px": 1080,
            "height_px": 1920,
        },
        subjects=["money"],
        usage={
            "use_class": "commercial",
            "advertising": True,
            "cta_required": True,
        },
        asset_readiness="no_assets",
        source_material="idea_only",
        content_depth="multi_beat",
    )
    plan = build_plan(profile)
    first = build_plan(profile)

    assert plan.profile_hash == first.profile_hash
    assert plan.decision.profile_hash == plan.profile_hash
    assert plan.recipe_selection.recipe.id == "explainer-short"
    roles = slot_roles(plan)
    assert {"hook", "primary_visual", "claim_or_proof", "cta", "captions"} <= set(roles)
    assert roles["cta"].exactness == "exact"
    assert "generated_cta_text" in roles["cta"].prohibited_operations
    assert roles["claim_or_proof"].grounding == ("claim_evidence",)
    assert roles["captions"].exactness == "exact"


def test_plan_ceiling_never_exceeds_decision_or_recipe():
    profile = make_profile(
        form="story",
        truth_policy="fictional",
        realistic_synthetic=True,
        asset_readiness="no_assets",
        source_material="idea_only",
    )
    plan = build_plan(profile)
    decision_rank = CEILING_ORDER.index(plan.decision.treatment_ceiling)
    recipe_rank = CEILING_ORDER.index(plan.recipe_selection.recipe.default_ceiling)
    bound = min(decision_rank, recipe_rank)
    for slot in plan.slots:
        assert CEILING_ORDER.index(slot.treatment_ceiling) <= bound


def test_product_slot_only_exists_for_product_roles():
    none_profile = make_profile(product_role="none")
    hero_profile = make_profile(
        product_role="hero",
        asset_readiness="photos_and_footage",
        primary_outcome="sell",
    )

    assert "product_reference" not in slot_roles(build_plan(none_profile))
    product_slot = slot_roles(build_plan(hero_profile))["product_reference"]
    assert product_slot.exactness == "exact"
    assert "generated_product_identity" in product_slot.prohibited_operations
    assert product_slot.treatment_ceiling == "match"


def test_series_list_plan_carries_novelty_gate():
    profile = make_profile(structures=["list"], series_pattern="series")
    plan = build_plan(profile)
    assert plan.recipe_selection.recipe.id == "trivia-list"
    assert "series_novelty_review" in plan.decision.required_gates


def test_record_creative_plan_creates_a_contract_artifact(controller, tmp_path):
    import json
    from argparse import Namespace

    from pinaka.cli import cmd_record_creative_plan

    from .conftest import create_project

    project = create_project(controller)
    profile = make_profile(subjects=["money"], primary_outcome="inform")
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(
        json.dumps(profile.model_dump(mode="json")), encoding="utf-8"
    )

    result = cmd_record_creative_plan(
        Namespace(
            home=controller.artifact_store.root,
            project_id=project.id,
            file=str(profile_path),
        )
    )

    assert result["artifact_id"]
    assert result["recipe"] == "explainer-short"
    assert len(result["content_hash"]) == 64
    artifact = controller.repository.get_artifact(result["artifact_id"])
    assert artifact["kind"] == "contract"
    stored = controller.read_artifact_text(result["artifact_id"])
    payload = json.loads(stored)
    assert payload["profile_hash"] == result["profile_hash"]
    assert payload["recipe_selection"]["recipe"]["id"] == "explainer-short"
    assert payload["slots"]
