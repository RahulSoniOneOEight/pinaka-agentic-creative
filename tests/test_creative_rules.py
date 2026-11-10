from __future__ import annotations

from pinaka.creative_rules import CreativeProfile, evaluate_profile


def make_profile(**overrides):
    payload = {
        "deliverable": {
            "output_kind": "video",
            "duration_seconds": "30",
            "orientation": "vertical",
            "aspect_ratio": "9:16",
            "width_px": 1080,
            "height_px": 1920,
            "destination": "social",
            "language": "en",
        },
        "product_role": "none",
        "form": "explainer",
        "structures": ["linear"],
        "subjects": ["lifestyle"],
        "tones": ["neutral"],
        "primary_outcome": "inform",
        "truth_policy": "source_bound",
        "usage": {
            "use_class": "noncommercial",
            "monetized": False,
            "advertising": False,
            "sponsored": False,
            "cta_required": False,
        },
        "asset_readiness": "photos_and_footage",
        "source_material": "own_footage_or_voice",
        "content_depth": "single_message",
    }
    payload.update(overrides)
    return CreativeProfile.model_validate(payload)


def rule_ids(profile):
    return {match.rule_id for match in evaluate_profile(profile).matched_rules}


def test_cr01_product_asset_gap():
    profile = make_profile(product_role="hero", asset_readiness="no_assets")
    decision = evaluate_profile(profile)
    assert "CR-01" in rule_ids(profile)
    assert decision.risk == "medium"
    assert decision.treatment_ceiling == "match"
    assert "product_asset_approval" in decision.required_gates


def test_cr02_sell_outcome_is_exact():
    profile = make_profile(primary_outcome="sell")
    decision = evaluate_profile(profile)
    assert "CR-02" in rule_ids(profile)
    assert decision.exactness == "exact"
    assert "commercial_terms_review" in decision.required_gates


def test_cr03_promotional_humour():
    profile = make_profile(
        tones=["humorous"],
        usage={
            "use_class": "commercial",
            "advertising": True,
            "cta_required": True,
        },
    )
    decision = evaluate_profile(profile)
    assert "CR-03" in rule_ids(profile)
    assert "brief_brand_fit" in decision.required_gates


def test_cr04_tutorial_requires_real_capture():
    profile = make_profile(form="tutorial")
    decision = evaluate_profile(profile)
    assert "CR-04" in rule_ids(profile)
    assert decision.treatment_ceiling == "match"
    assert "reproducibility_review" in decision.required_gates


def test_cr05_commentary_preserves_context():
    profile = make_profile(form="commentary")
    decision = evaluate_profile(profile)
    assert "CR-05" in rule_ids(profile)
    assert "context_review" in decision.required_gates
    assert "source_context" in decision.required_grounding


def test_cr06_news_comedy_needs_separation():
    profile = make_profile(subjects=["news"], tones=["humorous"])
    decision = evaluate_profile(profile)
    assert "CR-06" in rule_ids(profile)
    assert "satire_separation_review" in decision.required_gates


def test_cr07_money_requires_claim_signoff():
    profile = make_profile(subjects=["money"])
    decision = evaluate_profile(profile)
    assert "CR-07" in rule_ids(profile)
    assert decision.risk == "medium"
    assert "designated_signoff" in decision.required_gates


def test_cr08_kids_review():
    profile = make_profile(subjects=["kids"])
    decision = evaluate_profile(profile)
    assert "CR-08" in rule_ids(profile)
    assert "age_platform_review" in decision.required_gates


def test_cr09_realistic_synthetic_story_disclosure():
    profile = make_profile(
        form="story",
        truth_policy="fictional",
        realistic_synthetic=True,
    )
    decision = evaluate_profile(profile)
    assert "CR-09" in rule_ids(profile)
    assert "realism_disclosure_review" in decision.required_gates
    assert "synthetic_media_disclosure" in decision.disclosures


def test_cr10_sports_pop_culture_is_high_risk():
    profile = make_profile(
        subjects=["sports_pop_culture"],
        truth_policy="fictional",
        realistic_synthetic=True,
    )
    decision = evaluate_profile(profile)
    assert "CR-10" in rule_ids(profile)
    assert decision.risk == "high"
    assert "likeness_review" in decision.required_gates


def test_cr11_long_form_heavy_generation_cost_gate():
    profile = make_profile(
        deliverable={
            "output_kind": "video",
            "duration_seconds": "180",
            "orientation": "vertical",
            "aspect_ratio": "9:16",
            "width_px": 1080,
            "height_px": 1920,
        },
        heavy_generation=True,
    )
    decision = evaluate_profile(profile)
    assert "CR-11" in rule_ids(profile)
    assert "cost_deadline_review" in decision.required_gates


def test_cr12_short_format_with_long_form_depth():
    profile = make_profile(content_depth="long_form")
    decision = evaluate_profile(profile)
    assert "CR-12" in rule_ids(profile)
    assert "self_contained_review" in decision.required_gates


def test_cr13_idea_only_missing_assets():
    profile = make_profile(
        asset_readiness="no_assets",
        source_material="idea_only",
    )
    decision = evaluate_profile(profile)
    assert "CR-13" in rule_ids(profile)
    assert decision.treatment_ceiling == "ai_enhance"
    assert "generation_eligibility_review" in decision.required_gates


def test_cr14_urgent_emotional_animatic_gate():
    profile = make_profile(
        tones=["urgent"],
        secondary_outcomes=["evoke_emotion"],
    )
    decision = evaluate_profile(profile)
    assert "CR-14" in rule_ids(profile)
    assert "animatic_review" in decision.required_gates


def test_cr15_series_list_novelty_gate():
    profile = make_profile(
        structures=["list"],
        series_pattern="series",
    )
    decision = evaluate_profile(profile)
    assert "CR-15" in rule_ids(profile)
    assert "series_novelty_review" in decision.required_gates
    assert "series_bible" in decision.required_grounding


def test_conflict_resolution_unions_gates_and_tightens_constraints():
    profile = make_profile(
        subjects=["money"],
        primary_outcome="sell",
        structures=["list"],
        series_pattern="series",
        content_depth="long_form",
    )
    decision = evaluate_profile(profile)
    matched = rule_ids(profile)
    assert {"CR-02", "CR-07", "CR-12", "CR-15"}.issubset(matched)
    assert decision.exactness == "exact"
    assert decision.risk == "medium"
    assert "commercial_terms_review" in decision.required_gates
    assert "designated_signoff" in decision.required_gates
    assert "self_contained_review" in decision.required_gates
    assert "series_novelty_review" in decision.required_gates


def test_realistic_synthetic_factual_news_is_blocked():
    profile = make_profile(
        subjects=["news"],
        truth_policy="source_bound",
        realistic_synthetic=True,
    )
    decision = evaluate_profile(profile)
    assert decision.blocking
    assert decision.alternatives


def test_editorial_advertising_is_blocked():
    profile = make_profile(
        usage={
            "use_class": "editorial",
            "advertising": True,
            "cta_required": True,
        }
    )
    decision = evaluate_profile(profile)
    assert decision.blocking
