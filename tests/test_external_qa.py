from __future__ import annotations

import gzip

from pinaka.external_qa import parse_qctools_report, summarize_mediaconch


def test_qctools_report_parser_counts_frames(tmp_path):
    report = tmp_path / "report.qctools.xml.gz"
    with gzip.open(report, "wb") as handle:
        handle.write(
            b"<ffprobe><frames><frame pts='0'/><frame pts='1'/></frames></ffprobe>"
        )
    summary = parse_qctools_report(report)
    assert summary.parsed is True
    assert summary.frames == 2
    assert "2 frame" in summary.detail


def test_missing_qctools_report_is_not_parsed(tmp_path):
    summary = parse_qctools_report(tmp_path / "missing.gz")
    assert summary.parsed is False
    assert "missing" in summary.detail


def test_mediaconch_without_policy_is_a_warning():
    status, detail = summarize_mediaconch("N/A! video.mp4\n", 0)
    assert status == "warn"
    assert "N/A" in detail


def test_mediaconch_nonzero_exit_is_a_failure():
    status, detail = summarize_mediaconch("", 2)
    assert status == "fail"
    assert "code 2" in detail


def test_mediaconch_clean_parse_passes():
    status, detail = summarize_mediaconch("MediaConch report\n", 0)
    assert status == "pass"
    assert "parsed" in detail
