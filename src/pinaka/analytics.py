from __future__ import annotations

import csv
import io
import math
from typing import Any, Literal

from pydantic import Field

from .models import StrictModel
from .utils import sha256_json, utc_now

PrimaryMetric = Literal[
    "view_rate",
    "completion_rate",
    "click_through_rate",
    "conversion_rate",
]
Status = Literal["insufficient_sample", "winner", "no_difference", "invalid"]


class VariantMetrics(StrictModel):
    variant: str = Field(min_length=1, max_length=100)
    impressions: int = Field(ge=0)
    views: int = Field(ge=0)
    completions: int = Field(ge=0)
    clicks: int = Field(ge=0)
    conversions: int = Field(ge=0)


class ExperimentDesign(StrictModel):
    schema_version: str = "experiment-design/v1"
    experiment_id: str = Field(min_length=1, max_length=120)
    hypothesis: str = Field(min_length=1, max_length=1000)
    primary_metric: PrimaryMetric = "completion_rate"
    variants: tuple[str, ...] = Field(min_length=2)
    control: str
    minimum_sample_per_variant: int = Field(default=100, ge=1)
    alpha: float = Field(default=0.05, gt=0, lt=1)
    decision_rule: str = Field(
        default="Choose the variant with the highest rate only when its two-sided "
        "p-value beats the Bonferroni-adjusted alpha.",
        min_length=1,
        max_length=1000,
    )


class VariantResult(StrictModel):
    variant: str
    sample_size: int
    successes: int
    rate: float
    ci_low: float
    ci_high: float
    lift_vs_control: float | None = None
    p_value: float | None = None
    significant: bool = False


class ExperimentReport(StrictModel):
    schema_version: str = "experiment-report/v1"
    generated_at: str
    experiment_id: str
    status: Status
    primary_metric: PrimaryMetric
    control: str
    adjusted_alpha: float
    winner: str | None = None
    variants: tuple[VariantResult, ...] = ()
    notes: tuple[str, ...] = ()
    manifest_hash: str


REQUIRED_METRIC_COLUMNS = (
    "variant",
    "impressions",
    "views",
    "completions",
    "clicks",
    "conversions",
)


def ingest_metrics_csv(text: str) -> tuple[VariantMetrics, ...]:
    """Parse a platform export CSV with one row per variant.

    The expected columns are ``variant, impressions, views, completions, clicks,
    conversions``. Extra columns are ignored. Values must be non-negative integers.
    """

    content = text.lstrip("\ufeff").strip()
    if not content:
        raise ValueError("Metrics CSV is empty")
    reader = csv.DictReader(io.StringIO(content))
    headers = {name.strip() for name in (reader.fieldnames or [])}
    missing = [name for name in REQUIRED_METRIC_COLUMNS if name not in headers]
    if missing:
        raise ValueError(f"Metrics CSV is missing columns: {', '.join(missing)}")
    rows: list[VariantMetrics] = []
    for line_number, row in enumerate(reader, start=2):
        try:
            rows.append(
                VariantMetrics(
                    variant=str(row.get("variant") or "").strip(),
                    impressions=int(str(row.get("impressions") or "").strip()),
                    views=int(str(row.get("views") or "").strip()),
                    completions=int(str(row.get("completions") or "").strip()),
                    clicks=int(str(row.get("clicks") or "").strip()),
                    conversions=int(str(row.get("conversions") or "").strip()),
                )
            )
        except (TypeError, ValueError) as error:
            raise ValueError(f"Invalid metrics row {line_number}: {error}") from error
    if not rows:
        raise ValueError("Metrics CSV contains no data rows")
    return tuple(rows)


def _wilson(
    successes: int, sample: int, z: float = 1.959963984540054
) -> tuple[float, float]:
    if sample <= 0:
        return 0.0, 0.0
    rate = successes / sample
    denominator = 1 + z * z / sample
    centre = rate + z * z / (2 * sample)
    margin = z * math.sqrt(rate * (1 - rate) / sample + z * z / (4 * sample * sample))
    return (
        max(0.0, (centre - margin) / denominator),
        min(1.0, (centre + margin) / denominator),
    )


def _two_proportion_p(
    successes_a: int,
    sample_a: int,
    successes_b: int,
    sample_b: int,
) -> float:
    if sample_a <= 0 or sample_b <= 0:
        return 1.0
    pooled = (successes_a + successes_b) / (sample_a + sample_b)
    standard_error = math.sqrt(pooled * (1 - pooled) * (1 / sample_a + 1 / sample_b))
    if standard_error == 0:
        return 1.0
    difference = successes_a / sample_a - successes_b / sample_b
    z_value = abs(difference / standard_error)
    return math.erfc(z_value / math.sqrt(2))


def _counts(metrics: VariantMetrics, primary_metric: PrimaryMetric) -> tuple[int, int]:
    if primary_metric == "view_rate":
        return metrics.views, metrics.impressions
    if primary_metric == "completion_rate":
        return metrics.completions, metrics.views
    if primary_metric == "click_through_rate":
        return metrics.clicks, metrics.impressions
    return metrics.conversions, metrics.clicks


def evaluate_experiment(
    design: ExperimentDesign,
    metrics: list[VariantMetrics],
) -> ExperimentReport:
    notes: list[str] = []
    by_variant = {item.variant: item for item in metrics}
    if len(by_variant) != len(metrics):
        notes.append("Duplicate variant rows were supplied.")
    if design.control not in design.variants:
        notes.append("The control is not one of the declared variants.")
    if set(design.variants) != set(by_variant):
        missing = sorted(set(design.variants) - set(by_variant))
        extra = sorted(set(by_variant) - set(design.variants))
        if missing:
            notes.append(f"Missing metrics for: {', '.join(missing)}")
        if extra:
            notes.append(f"Unexpected metrics for: {', '.join(extra)}")
    if notes:
        body = {
            "generated_at": utc_now(),
            "experiment_id": design.experiment_id,
            "status": "invalid",
            "primary_metric": design.primary_metric,
            "control": design.control,
            "adjusted_alpha": design.alpha,
            "variants": [],
            "notes": notes,
        }
        return ExperimentReport(**body, manifest_hash=sha256_json(body))

    adjusted_alpha = design.alpha / max(1, len(design.variants) - 1)
    results: list[VariantResult] = []
    insufficient = False
    control_successes, control_sample = _counts(
        by_variant[design.control], design.primary_metric
    )
    control_rate = control_successes / control_sample if control_sample > 0 else 0.0
    for variant in design.variants:
        successes, sample = _counts(by_variant[variant], design.primary_metric)
        rate = successes / sample if sample > 0 else 0.0
        low, high = _wilson(successes, sample)
        if sample < design.minimum_sample_per_variant or sample == 0:
            insufficient = True
            notes.append(
                f"{variant} has {sample} samples; the minimum is "
                f"{design.minimum_sample_per_variant}."
            )
        lift = (
            (rate - control_rate) / control_rate
            if control_rate > 0 and variant != design.control
            else None
        )
        p_value = (
            _two_proportion_p(successes, sample, control_successes, control_sample)
            if variant != design.control
            else None
        )
        results.append(
            VariantResult(
                variant=variant,
                sample_size=sample,
                successes=successes,
                rate=round(rate, 6),
                ci_low=round(low, 6),
                ci_high=round(high, 6),
                lift_vs_control=round(lift, 6) if lift is not None else None,
                p_value=round(p_value, 8) if p_value is not None else None,
                significant=bool(p_value is not None and p_value < adjusted_alpha),
            )
        )

    if insufficient:
        status: Status = "insufficient_sample"
        winner = None
    else:
        candidates = [
            result
            for result in results
            if result.variant != design.control
            and result.significant
            and (result.lift_vs_control or 0) > 0
        ]
        if candidates:
            winner_result = max(candidates, key=lambda item: item.rate)
            status = "winner"
            winner = winner_result.variant
        else:
            status = "no_difference"
            winner = None

    body = {
        "generated_at": utc_now(),
        "experiment_id": design.experiment_id,
        "status": status,
        "primary_metric": design.primary_metric,
        "control": design.control,
        "adjusted_alpha": round(adjusted_alpha, 8),
        "winner": winner,
        "variants": [result.model_dump(mode="json") for result in results],
        "notes": notes,
    }
    return ExperimentReport(**body, manifest_hash=sha256_json(body))


def experiment_payload(report: ExperimentReport) -> dict[str, Any]:
    return report.model_dump(mode="json")
