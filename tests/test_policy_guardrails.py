from __future__ import annotations

import pytest

from pinaka.errors import IntegrityError
from pinaka.policy.guards import require_one_take


@pytest.mark.parametrize(
    "parameters",
    [
        {"num_images": 2},
        {"num_images": 0},
        {"num_outputs": 2},
        {"num_variants": 2},
        {"batch_size": 4},
        {"batch_count": 2},
        {"n": 2},
        {"count": 3},
        {"samples": 2},
        {"variants": 2},
        {"outputs": 2},
        {"results": 2},
        {"auto_retry": True},
        {"retries": 1},
        {"max_retries": 2},
    ],
)
def test_any_known_multiplier_is_refused(parameters):
    with pytest.raises(IntegrityError, match="one take"):
        require_one_take(parameters)


@pytest.mark.parametrize(
    "parameters",
    [
        {},
        {"num_images": 1},
        {"n": 1},
        {"auto_retry": False},
        {"retries": 0},
        {"reference_images": ["a.png", "b.png"]},
        {"prompt": "a single still"},
    ],
)
def test_single_take_and_legitimate_reference_lists_pass(parameters):
    require_one_take(parameters)
