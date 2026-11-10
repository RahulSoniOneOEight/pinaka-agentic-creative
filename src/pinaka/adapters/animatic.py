from __future__ import annotations

from typing import Any, ClassVar

from ..animatic import render_html, render_srt, render_timeline
from ..models import ToolOperationRequest
from ..utils import sha256_bytes
from .tool import ToolExecutionResult


class DeterministicAnimaticAdapter:
    """Local renderer for the approved pilot animatic, captions and timeline."""

    profile_key = "deterministic-animatic"

    _OPERATIONS: ClassVar[dict[str, Any]] = {
        "render_timeline": render_timeline,
        "render_animatic": render_html,
        "render_captions": render_srt,
    }

    def execute(self, request: ToolOperationRequest) -> ToolExecutionResult:
        renderer = self._OPERATIONS.get(request.operation)
        if renderer is None:
            raise ValueError(f"Unsupported animatic operation: {request.operation}")
        timeline: Any = request.parameters.get("timeline")
        if not isinstance(timeline, dict):
            raise TypeError("Animatic operation requires a timeline object")
        output = renderer(timeline)
        return ToolExecutionResult(
            output_bytes=output,
            details={
                "renderer": self.profile_key,
                "operation": request.operation,
                "timeline_hash": timeline.get("timeline_hash"),
                "paid": False,
            },
            response_hash=sha256_bytes(output),
        )
