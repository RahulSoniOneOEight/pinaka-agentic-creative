from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .errors import ConfigurationError
from .utils import sha256_bytes, sha256_json

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


@dataclass(frozen=True)
class EditResult:
    content: bytes
    content_hash: str
    workflow_hash: str
    operation: dict[str, Any]


def _assert_loopback(base_url: str) -> None:
    parsed = urllib.parse.urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in LOOPBACK_HOSTS:
        raise ConfigurationError(
            "Local AI editing is limited to a loopback ComfyUI endpoint"
        )


def build_img2img_workflow(
    *,
    image_name: str,
    checkpoint: str,
    prompt: str,
    negative_prompt: str,
    seed: int,
    steps: int,
    denoise: float,
) -> dict[str, Any]:
    return {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": checkpoint},
        },
        "2": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "3": {
            "class_type": "VAEEncode",
            "inputs": {"pixels": ["2", 0], "vae": ["1", 2]},
        },
        "4": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": prompt, "clip": ["1", 1]},
        },
        "5": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": negative_prompt, "clip": ["1", 1]},
        },
        "6": {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed,
                "steps": steps,
                "cfg": 7.0,
                "sampler_name": "euler",
                "scheduler": "normal",
                "denoise": denoise,
                "model": ["1", 0],
                "positive": ["4", 0],
                "negative": ["5", 0],
                "latent_image": ["3", 0],
            },
        },
        "7": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["6", 0], "vae": ["1", 2]},
        },
        "8": {
            "class_type": "SaveImage",
            "inputs": {"images": ["7", 0], "filename_prefix": "pinaka-edit"},
        },
    }


def build_inpaint_workflow(
    *,
    image_name: str,
    mask_name: str,
    checkpoint: str,
    prompt: str,
    negative_prompt: str,
    seed: int,
    steps: int,
    denoise: float,
) -> dict[str, Any]:
    graph = build_img2img_workflow(
        image_name=image_name,
        checkpoint=checkpoint,
        prompt=prompt,
        negative_prompt=negative_prompt,
        seed=seed,
        steps=steps,
        denoise=denoise,
    )
    graph["2"] = {"class_type": "LoadImage", "inputs": {"image": image_name}}
    graph["9"] = {
        "class_type": "LoadImageMask",
        "inputs": {"image": mask_name, "channel": "red"},
    }
    graph["3"] = {
        "class_type": "VAEEncodeForInpaint",
        "inputs": {
            "pixels": ["2", 0],
            "vae": ["1", 2],
            "mask": ["9", 0],
            "grow_mask_by": 6,
        },
    }
    return graph


def build_upscale_workflow(
    *,
    image_name: str,
    model_name: str,
) -> dict[str, Any]:
    return {
        "1": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "2": {
            "class_type": "UpscaleModelLoader",
            "inputs": {"model_name": model_name},
        },
        "3": {
            "class_type": "ImageUpscaleWithModel",
            "inputs": {"upscale_model": ["2", 0], "image": ["1", 0]},
        },
        "4": {
            "class_type": "SaveImage",
            "inputs": {"images": ["3", 0], "filename_prefix": "pinaka-upscale"},
        },
    }


class ComfyUIEditAdapter:
    """AI edit and generative upscale through a loopback ComfyUI server."""

    profile_key = "comfyui-ai-edit"

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8188",
        *,
        allowed_checkpoints: tuple[str, ...],
        allowed_upscale_models: tuple[str, ...],
        timeout_seconds: int = 300,
        transport: Callable[[str, str, Any], bytes] | None = None,
        clock: Callable[[], float] = time.monotonic,
        poll_interval: float = 1.0,
    ):
        _assert_loopback(base_url)
        if not allowed_checkpoints and not allowed_upscale_models:
            raise ValueError(
                "At least one checkpoint or upscale model must be allowlisted"
            )
        self.base_url = base_url.rstrip("/")
        self.allowed_checkpoints = set(allowed_checkpoints)
        self.allowed_upscale_models = set(allowed_upscale_models)
        self.timeout_seconds = timeout_seconds
        self.transport = transport
        self.clock = clock
        self.poll_interval = poll_interval

    def _request(self, method: str, path: str, payload: Any | None = None) -> bytes:
        if self.transport is not None:
            return self.transport(method, f"{self.base_url}{path}", payload)
        data = None
        headers = {"Content-Type": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=data, headers=headers, method=method
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read()

    def _upload(self, content: bytes, filename: str) -> str:
        boundary = uuid.uuid4().hex
        body = b"".join(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="image"; filename="{filename}"\r\n'.encode(),
                b"Content-Type: application/octet-stream\r\n\r\n",
                content,
                f"\r\n--{boundary}--\r\n".encode(),
            ]
        )
        if self.transport is not None:
            raw = self.transport("POST", f"{self.base_url}/upload/image", body)
        else:
            request = urllib.request.Request(
                f"{self.base_url}/upload/image",
                data=body,
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=120) as response:
                raw = response.read()
        return str(json.loads(raw.decode("utf-8"))["name"])

    def _run(self, workflow: dict[str, Any]) -> bytes:
        created = json.loads(
            self._request(
                "POST",
                "/prompt",
                {"prompt": workflow, "client_id": "pinaka-controller"},
            ).decode("utf-8")
        )
        prompt_id = created.get("prompt_id")
        if not prompt_id:
            raise ConfigurationError("ComfyUI did not return a prompt id")
        deadline = self.clock() + self.timeout_seconds
        while self.clock() < deadline:
            history = json.loads(
                self._request("GET", f"/history/{prompt_id}").decode("utf-8")
            )
            entry = history.get(prompt_id)
            if entry and entry.get("outputs"):
                for output in entry["outputs"].values():
                    for image in output.get("images", []):
                        query = urllib.parse.urlencode(
                            {
                                "filename": image["filename"],
                                "subfolder": image.get("subfolder", ""),
                                "type": image.get("type", "output"),
                            }
                        )
                        return self._request("GET", f"/view?{query}")
            time.sleep(self.poll_interval)
        raise ConfigurationError("ComfyUI edit timed out")

    def edit(
        self,
        source: bytes,
        *,
        prompt: str,
        negative_prompt: str = "",
        checkpoint: str | None = None,
        mask: bytes | None = None,
        seed: int = 0,
        steps: int = 20,
        denoise: float = 0.55,
    ) -> EditResult:
        checkpoint = checkpoint or next(iter(self.allowed_checkpoints))
        if checkpoint not in self.allowed_checkpoints:
            raise ValueError(f"Checkpoint is not allowlisted: {checkpoint!r}")
        if not prompt.strip():
            raise ValueError("An edit prompt is required")
        image_name = self._upload(source, "source.png")
        if mask is not None:
            mask_name = self._upload(mask, "mask.png")
            workflow = build_inpaint_workflow(
                image_name=image_name,
                mask_name=mask_name,
                checkpoint=checkpoint,
                prompt=prompt,
                negative_prompt=negative_prompt,
                seed=seed,
                steps=steps,
                denoise=denoise,
            )
            kind = "ai_inpaint"
        else:
            workflow = build_img2img_workflow(
                image_name=image_name,
                checkpoint=checkpoint,
                prompt=prompt,
                negative_prompt=negative_prompt,
                seed=seed,
                steps=steps,
                denoise=denoise,
            )
            kind = "ai_edit"
        content = self._run(workflow)
        return EditResult(
            content=content,
            content_hash=sha256_bytes(content),
            workflow_hash=sha256_json(workflow),
            operation={
                "type": kind,
                "prompt": prompt,
                "negative_prompt": negative_prompt,
                "checkpoint": checkpoint,
                "seed": seed,
                "steps": steps,
                "denoise": denoise,
                "workflow_hash": sha256_json(workflow),
            },
        )

    def upscale(self, source: bytes, *, model_name: str | None = None) -> EditResult:
        model_name = model_name or next(iter(self.allowed_upscale_models), None)
        if not model_name:
            raise ConfigurationError(
                "No upscale model is allowlisted; install an ESRGAN-class model"
            )
        if model_name not in self.allowed_upscale_models:
            raise ValueError(f"Upscale model is not allowlisted: {model_name!r}")
        image_name = self._upload(source, "source.png")
        workflow = build_upscale_workflow(image_name=image_name, model_name=model_name)
        content = self._run(workflow)
        return EditResult(
            content=content,
            content_hash=sha256_bytes(content),
            workflow_hash=sha256_json(workflow),
            operation={
                "type": "generative_upscale",
                "model_name": model_name,
                "workflow_hash": sha256_json(workflow),
            },
        )
