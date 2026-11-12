from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts.failed_tests import cached_failure_ids


def test_empty_failure_cache_never_selects_the_full_suite(tmp_path):
    assert cached_failure_ids(tmp_path) == []


def test_cached_failures_filter_retired_tests_and_keep_parameterized_ids(tmp_path):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_sample.py").write_text(
        "def test_current():\n    pass\n", encoding="utf-8"
    )
    cache = tmp_path / ".pytest_cache" / "v" / "cache"
    cache.mkdir(parents=True)
    (cache / "lastfailed").write_text(
        json.dumps(
            {
                "tests/test_sample.py::test_retired": True,
                "tests/test_missing.py::test_old": True,
                "tests/test_sample.py::test_current[example]": True,
            }
        ),
        encoding="utf-8",
    )
    assert cached_failure_ids(tmp_path) == [
        "tests/test_sample.py::test_current[example]"
    ]


def test_all_stale_ids_select_nothing(tmp_path):
    (tmp_path / "tests").mkdir()
    cache = tmp_path / ".pytest_cache" / "v" / "cache"
    cache.mkdir(parents=True)
    (cache / "lastfailed").write_text(
        '{"tests/test_removed.py::test_old": true}', encoding="utf-8"
    )
    assert cached_failure_ids(tmp_path) == []


@pytest.mark.slow
@pytest.mark.skipif(
    shutil.which("powershell.exe") is None,
    reason="Windows PowerShell interoperability check",
)
@pytest.mark.parametrize("ids", [[], ["tests/test_sample.py::test_current"]])
def test_powershell_selector_does_not_pass_a_nested_empty_array_to_pytest(ids):
    script = Path(__file__).resolve().parents[1] / "scripts" / "quick.ps1"
    selection = next(
        line.strip()
        for line in script.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("$selectedIds = @(")
    )
    command = f"$selectionJson = '{json.dumps(ids)}'\n{selection}\nif ($selectedIds.Count -ne {len(ids)}) {{ exit 1 }}"
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
