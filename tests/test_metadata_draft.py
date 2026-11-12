from __future__ import annotations

import pytest

from pinaka.metadata import draft_metadata
from tests.conftest import create_project


def test_draft_is_built_from_approved_fields():
    draft = draft_metadata(
        name="Virtual CFO in 10 seconds",
        objective="Explain the Virtual CFO offer for founders.",
        channel="youtube_shorts",
        claims=("Human-approved claim", "No invented numbers"),
        cta="Start a conversation",
        hashtags=("fintech", "#Founders", "fintech"),
    )

    assert draft["title"] == "Virtual CFO in 10 seconds"
    assert "Human-approved claim" in draft["description"]
    assert draft["description"].endswith("Start a conversation")
    assert draft["hashtags"] == ["#fintech", "#Founders"]
    assert draft["within_limits"] is True


def test_title_is_truncated_at_a_word_boundary():
    draft = draft_metadata(
        name="One " * 80,
        objective="Objective",
        channel="youtube_shorts",
    )

    assert len(draft["title"]) <= 100
    assert draft["title"].endswith("…")
    assert "  " not in draft["title"]


def test_description_respects_the_channel_limit():
    draft = draft_metadata(
        name="Title",
        objective="word " * 2000,
        channel="instagram_reels",
    )

    assert len(draft["description"]) <= draft["limits"]["description_chars"]
    assert draft["within_limits"] is True


def test_unknown_channel_is_refused():
    with pytest.raises(ValueError, match="Unknown channel"):
        draft_metadata(name="Title", objective="Objective", channel="myspace")


def test_cli_drafts_from_a_project(controller, tmp_path):
    from pinaka.cli import main

    project = create_project(controller, budget="10.00")
    output = tmp_path / "metadata.json"
    home = controller.database.path.parent.parent

    code = main(
        [
            "--home",
            str(home),
            "draft-metadata",
            project.id,
            "--channel",
            "youtube_shorts",
            "--claim",
            "Approved claim",
            "--output",
            str(output),
        ]
    )

    assert code == 0
    assert output.is_file()
    assert "Approved claim" in output.read_text(encoding="utf-8")
