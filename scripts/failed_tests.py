"""Select still-existing cached test IDs without pytest's run-all fallback."""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path


def cached_failure_ids(repo: Path) -> list[str]:
    cache = repo / ".pytest_cache" / "v" / "cache" / "lastfailed"
    if not cache.is_file():
        return []
    selected = []
    for nodeid, failed in json.loads(cache.read_text(encoding="utf-8")).items():
        parts = nodeid.split("::")
        path = (repo / parts[0]).resolve()
        if (
            not failed
            or not path.is_relative_to((repo / "tests").resolve())
            or not path.is_file()
        ):
            continue
        definitions = {
            n.name
            for n in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(n, (ast.FunctionDef, ast.ClassDef))
        }
        if len(parts) > 1 and all(
            part.split("[", 1)[0] in definitions for part in parts[1:]
        ):
            selected.append(nodeid)
    return sorted(selected)


if __name__ == "__main__":
    print(json.dumps(cached_failure_ids(Path(sys.argv[1]).resolve())))
