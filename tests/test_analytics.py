from __future__ import annotations

from pinaka.analytics import ExperimentDesign, VariantMetrics, evaluate_experiment


def design(**overrides):
    payload = {
        "experiment_id": "exp-001",
        "hypothesis": "A clearer hook raises completion rate.",
        "primary_metric": "completion_rate",
        "variants": ["control", "clear_hook"],
        "control": "control",
        "minimum_sample_per_variant": 100,
        "alpha": 0.05,
    }
    payload.update(overrides)
    return ExperimentDesign.model_validate(payload)


def metric(
    variant, *, impressions=1000, views=500, completions=100, clicks=50, conversions=10
):
    return VariantMetrics(
        variant=variant,
        impressions=impressions,
        views=views,
        completions=completions,
        clicks=clicks,
        conversions=conversions,
    )


def test_clear_winner_is_detected():
    report = evaluate_experiment(
        design(),
        [metric("control"), metric("clear_hook", completions=150)],
    )
    assert report.status == "winner"
    assert report.winner == "clear_hook"
    winner = next(item for item in report.variants if item.variant == "clear_hook")
    assert winner.lift_vs_control == 0.5
    assert winner.p_value is not None and winner.p_value < 0.05
    assert winner.ci_low < winner.rate < winner.ci_high


def test_insufficient_sample_blocks_a_winner_claim():
    report = evaluate_experiment(
        design(minimum_sample_per_variant=1000),
        [metric("control"), metric("clear_hook", completions=150)],
    )
    assert report.status == "insufficient_sample"
    assert report.winner is None
    assert any("minimum" in note for note in report.notes)


def test_no_difference_when_lift_is_within_noise():
    report = evaluate_experiment(
        design(),
        [metric("control"), metric("clear_hook", completions=105)],
    )
    assert report.status == "no_difference"
    assert report.winner is None


def test_variant_set_mismatch_is_invalid():
    report = evaluate_experiment(design(), [metric("control")])
    assert report.status == "invalid"
    assert any("Missing metrics" in note for note in report.notes)


def test_click_through_rate_uses_impressions():
    report = evaluate_experiment(
        design(primary_metric="click_through_rate"),
        [
            metric("control", clicks=50),
            metric("clear_hook", clicks=90),
        ],
    )
    assert report.status == "winner"
    assert report.winner == "clear_hook"
    winner = next(item for item in report.variants if item.variant == "clear_hook")
    assert winner.sample_size == 1000
    assert winner.successes == 90


def test_zero_denominator_is_invalid_sample():
    report = evaluate_experiment(
        design(primary_metric="conversion_rate"),
        [
            metric("control", clicks=0, conversions=0),
            metric("clear_hook", clicks=0, conversions=0),
        ],
    )
    assert report.status == "insufficient_sample"
    assert report.winner is None


def test_bonferroni_adjusts_alpha_for_multiple_variants():
    report = evaluate_experiment(
        design(variants=["control", "a", "b"], control="control"),
        [
            metric("control"),
            metric("a", completions=115),
            metric("b", completions=150),
        ],
    )
    assert report.adjusted_alpha == 0.025
    assert report.status == "winner"
    assert report.winner == "b"


def test_metrics_csv_is_ingested():
    from pinaka.analytics import ingest_metrics_csv

    rows = ingest_metrics_csv(
        "variant,impressions,views,completions,clicks,conversions\n"
        "control,1000,500,100,50,10\n"
        "hook,1000,500,150,60,12\n"
    )
    assert len(rows) == 2
    assert rows[1].variant == "hook"
    assert rows[1].completions == 150


def test_metrics_csv_accepts_a_bom_and_extra_columns():
    from pinaka.analytics import ingest_metrics_csv

    rows = ingest_metrics_csv(
        "\ufeffvariant,impressions,views,completions,clicks,conversions,spend\n"
        "control,1000,500,100,50,10,0\n"
    )
    assert rows[0].variant == "control"


def test_metrics_csv_rejects_missing_columns_and_bad_values():
    import pytest

    from pinaka.analytics import ingest_metrics_csv

    with pytest.raises(ValueError, match="missing columns"):
        ingest_metrics_csv("variant,impressions\ncontrol,10\n")
    with pytest.raises(ValueError, match="Invalid metrics row"):
        ingest_metrics_csv(
            "variant,impressions,views,completions,clicks,conversions\n"
            "control,not-a-number,500,100,50,10\n"
        )
