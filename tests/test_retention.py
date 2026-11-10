from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pinaka.retention import RetentionItem, RetentionPolicy, plan_retention

NOW = datetime(2026, 11, 10, tzinfo=timezone.utc)


def item(path, retention_class, days_old, *, pinned=False, size=100):
    return RetentionItem(
        path=path,
        retention_class=retention_class,
        created_at=NOW - timedelta(days=days_old),
        size_bytes=size,
        pinned=pinned,
    )


def test_permanent_and_archive_items_are_retained():
    report = plan_retention(
        [
            item("workspace/masters/master.mp4", "master", 1000),
            item("workspace/projects/p/archive/manifest.json", "temp", 1000),
            item("workspace/projects/p/archive/clip.tmp", "temp", 1000),
        ],
        now=NOW,
    )
    assert report.counts["purge"] == 0
    assert all(decision.action == "keep" for decision in report.items)


def test_expired_derivative_and_review_are_marked_for_purge():
    report = plan_retention(
        [
            item("workspace/formats/1x1.mp4", "derivative", 400, size=250),
            item("workspace/reviews/review.mp4", "review", 120, size=50),
            item("workspace/temp/scratch.txt", "temp", 1, size=10),
        ],
        now=NOW,
    )
    actions = {decision.path: decision.action for decision in report.items}
    assert actions["workspace/formats/1x1.mp4"] == "purge"
    assert actions["workspace/reviews/review.mp4"] == "purge"
    assert actions["workspace/temp/scratch.txt"] == "keep"
    assert report.reclaimed_bytes == 300


def test_pinned_item_is_retained_even_when_expired():
    report = plan_retention(
        [item("workspace/temp/pinned.txt", "temp", 99, pinned=True)],
        now=NOW,
    )
    assert report.items[0].action == "keep"
    assert report.items[0].reason == "Item is pinned."


def test_missing_window_requires_review():
    policy = RetentionPolicy(classes={"review": None})
    report = plan_retention(
        [item("workspace/reviews/a.txt", "review", 400)],
        now=NOW,
        policy=policy,
    )
    assert report.items[0].action == "review"


def test_report_is_dry_run_and_hashed():
    report = plan_retention([item("workspace/temp/a.txt", "temp", 8)], now=NOW)
    assert report.dry_run is True
    assert len(report.policy_hash) == 64
    assert report.items[0].expires_at
