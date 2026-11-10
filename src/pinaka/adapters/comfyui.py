from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

from ..errors import ConfigurationError
from ..models import ToolOperationRequest
from ..utils import sha256_bytes, sha256_json
from .tool import ToolExecutionResult

DEFAULT_BASE_URL = "http://127.0.0.1:8188"
DEFAULT_CHECKPOINT = "v1-5-pruned-emaonly-fp16.safetensors"
ALLOWED_NODE_TYPES = {
    "CheckpointLoaderSimple",
    "CLIPTextEncode",
    "EmptyLatentImage",
    "KSampler",
    "VAEDecode",
    "SaveImage",
}
MIN_DIMENSION = 64
MAX_DIMENSION = 1024
MAX_STEPS = 50
MAX_SEED = 2**63 - 1
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff"


def _local_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("ComfyUI base URL must use http or https")
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("ComfyUI adapter only connects to a loopback address")
    return base_url.rstrip("/")


def _integer(value: Any, name: str, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be an integer") from error
    if not minimum <= parsed <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return parsed


def _dimension(value: Any, name: str) -> int:
    parsed = _integer(value, name, MIN_DIMENSION, MAX_DIMENSION)
    if parsed % 8:
        raise ValueError(f"{name} must be a multiple of 8")
    return parsed


def _safe_prefix(value: str) -> str:
    prefix = value.strip() or "pinaka"
    if any(character in prefix for character in ("/", "\\", "..", ":")):
        raise ValueError("filename_prefix must be a simple file stem")
    return prefix


def validate_workflow(
    workflow: dict[str, Any], *, allowed_checkpoints: set[str]
) -> None:
    if not isinstance(workflow, dict) or not workflow:
        raise ValueError("Workflow must be a non-empty object")
    node_types: set[str] = set()
    for node_id, node in workflow.items():
        if not isinstance(node_id, str) or not isinstance(node, dict):
            raise TypeError("Workflow nodes must be objects keyed by node id")
        node_type = node.get("class_type")
        if not isinstance(node_type, str) or node_type not in ALLOWED_NODE_TYPES:
            raise ValueError(f"Node type is not allowed: {node_type!r}")
        node_types.add(node_type)
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            raise TypeError(f"Node {node_id} has no inputs object")
        for name, value in inputs.items():
            if isinstance(value, list):
                if (
                    len(value) != 2
                    or not isinstance(value[0], str)
                    or value[0] not in workflow
                    or not isinstance(value[1], int)
                ):
                    raise ValueError(f"Node {node_id}.{name} has an invalid link")
            elif isinstance(value, dict):
                raise TypeError(f"Node {node_id}.{name} must not be an object")
        if node_type == "CheckpointLoaderSimple":
            checkpoint = inputs.get("ckpt_name")
            if checkpoint not in allowed_checkpoints:
                raise ValueError(f"Checkpoint is not allowlisted: {checkpoint!r}")
        if node_type == "SaveImage":
            _safe_prefix(str(inputs.get("filename_prefix", "")))
    required = {
        "CheckpointLoaderSimple",
        "CLIPTextEncode",
        "EmptyLatentImage",
        "KSampler",
        "VAEDecode",
        "SaveImage",
    }
    if not required <= node_types:
        missing = ", ".join(sorted(required - node_types))
        raise ValueError(f"Workflow is missing required node types: {missing}")


def build_workflow(
    *,
    prompt: str,
    negative_prompt: str,
    checkpoint: str,
    width: int,
    height: int,
    steps: int,
    seed: int,
    filename_prefix: str = "pinaka",
) -> dict[str, Any]:
    return {
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed,
                "steps": steps,
                "cfg": 7.0,
                "sampler_name": "euler",
                "scheduler": "normal",
                "denoise": 1.0,
                "model": ["4", 0],
                "positive": ["6", 0],
                "negative": ["7", 0],
                "latent_image": ["5", 0],
            },
        },
        "4": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": checkpoint},
        },
        "5": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": width, "height": height, "batch_size": 1},
        },
        "6": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": prompt, "clip": ["4", 1]},
        },
        "7": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": negative_prompt, "clip": ["4", 1]},
        },
        "8": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["3", 0], "vae": ["4", 2]},
        },
        "9": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": filename_prefix, "images": ["8", 0]},
        },
    }


def _image_from_history(
    payload: dict[str, Any], prompt_id: str
) -> dict[str, Any] | None:
    entry = payload.get(prompt_id)
    if not entry:
        return None
    status = entry.get("status") or {}
    if status.get("status_str") == "error":
        raise RuntimeError("ComfyUI reported an execution error")
    for output in (entry.get("outputs") or {}).values():
        images = output.get("images") if isinstance(output, dict) else None
        if images:
            image = images[0]
            if isinstance(image, dict) and image.get("filename"):
                return image
    return None


class ComfyUIImageAdapter:
    """Controller-governed local text-to-image adapter.

    The adapter only speaks to loopback ComfyUI, only accepts allowlisted checkpoints,
    and refuses any non-zero cost declaration.
    """

    profile_key = "comfyui-local-image"

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        allowed_checkpoints: tuple[str, ...] = (DEFAULT_CHECKPOINT,),
        transport: Callable[[str, str, dict[str, Any] | None], bytes] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        poll_interval: float = 2.0,
        timeout_seconds: float = 900.0,
        request_timeout: float = 60.0,
    ):
        self.base_url = _local_url(base_url)
        self.allowed_checkpoints = set(allowed_checkpoints)
        if not self.allowed_checkpoints:
            raise ValueError("At least one checkpoint must be allowlisted")
        self.transport = transport
        self.sleep = sleep
        self.monotonic = monotonic
        self.poll_interval = poll_interval
        self.timeout_seconds = timeout_seconds
        self.request_timeout = request_timeout

    def _request(
        self, method: str, url: str, payload: dict[str, Any] | None = None
    ) -> bytes:
        if self.transport is not None:
            return self.transport(method, url, payload)
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method=method,
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self.request_timeout
            ) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            raise RuntimeError(
                f"ComfyUI request failed with HTTP {error.code}"
            ) from None
        except urllib.error.URLError as error:
            raise ConfigurationError(
                "ComfyUI is not reachable on the configured loopback address"
            ) from error

    def ensure_available(self) -> None:
        raw = self._request("GET", f"{self.base_url}/system_stats")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as error:
            raise ConfigurationError(
                "ComfyUI returned an invalid status response"
            ) from error
        if "system" not in payload:
            raise ConfigurationError("ComfyUI status response is missing system data")

    def _workflow(self, request: ToolOperationRequest) -> dict[str, Any]:
        parameters = request.parameters
        prompt = str(parameters.get("prompt") or "").strip()
        if not prompt:
            raise ValueError("An image prompt is required")
        if len(prompt) > 4000:
            raise ValueError("The image prompt exceeds 4000 characters")
        negative = str(
            parameters.get(
                "negative_prompt",
                "text, watermark, logo, letters, blurry, low quality",
            )
        ).strip()
        checkpoint = str(parameters.get("checkpoint") or DEFAULT_CHECKPOINT)
        if checkpoint not in self.allowed_checkpoints:
            raise ValueError(f"Checkpoint is not allowlisted: {checkpoint!r}")
        width = _dimension(parameters.get("width", 512), "width")
        height = _dimension(parameters.get("height", 512), "height")
        steps = _integer(parameters.get("steps", 12), "steps", 1, MAX_STEPS)
        seed = _integer(parameters.get("seed", 0), "seed", 0, MAX_SEED)
        prefix = _safe_prefix(str(parameters.get("filename_prefix", "pinaka")))
        workflow = build_workflow(
            prompt=prompt,
            negative_prompt=negative,
            checkpoint=checkpoint,
            width=width,
            height=height,
            steps=steps,
            seed=seed,
            filename_prefix=prefix,
        )
        if request.limits.get("allow_custom_workflow") is True:
            custom = parameters.get("workflow")
            if not isinstance(custom, dict):
                raise ValueError("allow_custom_workflow requires a workflow object")
            validate_workflow(custom, allowed_checkpoints=self.allowed_checkpoints)
            workflow = custom
        return workflow

    def execute(self, request: ToolOperationRequest) -> ToolExecutionResult:
        if request.operation != "image.generate":
            raise ValueError(f"ComfyUI adapter does not support {request.operation!r}")
        if request.limits.get("max_cost_minor") != 0:
            raise ValueError(
                "ComfyUI adapter is local and zero-cost only; set max_cost_minor to 0"
            )
        workflow = self._workflow(request)
        started = self.monotonic()
        raw = self._request("POST", f"{self.base_url}/prompt", {"prompt": workflow})
        submitted = json.loads(raw.decode("utf-8"))
        prompt_id = str(submitted.get("prompt_id") or "")
        if not prompt_id:
            raise RuntimeError("ComfyUI did not return a prompt id")
        deadline = started + self.timeout_seconds
        image: dict[str, Any] | None = None
        while self.monotonic() < deadline:
            history_raw = self._request("GET", f"{self.base_url}/history/{prompt_id}")
            image = _image_from_history(
                json.loads(history_raw.decode("utf-8")), prompt_id
            )
            if image:
                break
            self.sleep(self.poll_interval)
        if image is None:
            raise TimeoutError("ComfyUI generation timed out")
        query = urllib.parse.urlencode(
            {
                "filename": image["filename"],
                "subfolder": image.get("subfolder", ""),
                "type": image.get("type", "output"),
            }
        )
        content = self._request("GET", f"{self.base_url}/view?{query}")
        if not content.startswith((PNG_MAGIC, JPEG_MAGIC)):
            raise RuntimeError("ComfyUI returned a non-image payload")
        elapsed = max(0.0, self.monotonic() - started)
        return ToolExecutionResult(
            output_bytes=content,
            details={
                "adapter": self.profile_key,
                "prompt_id": prompt_id,
                "workflow_hash": sha256_json(workflow),
                "prompt_sha256": sha256_json(
                    {"prompt": str(request.parameters.get("prompt", ""))}
                ),
                "checkpoint": str(
                    request.parameters.get("checkpoint") or DEFAULT_CHECKPOINT
                ),
                "device": "cpu",
                "elapsed_seconds": round(elapsed, 3),
                "image_bytes": len(content),
                "cost_minor": 0,
            },
            response_hash=sha256_bytes(content),
        )
