"""
BorderRender — Job definitions

Pricing: BC per frame (image = 1 frame, video = N frames, 3D = N frames).
Every render = a ComputeProofRecord submitted to BorderChain.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

# Default pricing
BC_PER_IMAGE_512  = 0.0005     # 512×512 image
BC_PER_IMAGE_1024 = 0.002      # 1024×1024 (SDXL quality)
BC_PER_VIDEO_FRAME= 0.0003     # per video frame
BC_PER_3D_FRAME   = 0.001      # Blender render frame
MIN_STAKE_TO_RENDER = 10.0     # BC worker must stake


class RenderType(str, Enum):
    IMAGE  = "image"    # Stable Diffusion / FLUX image generation
    VIDEO  = "video"    # AnimateDiff / Wan video
    MODEL3D= "model3d"  # TripoSR / InstantMesh 3D model
    UPSCALE= "upscale"  # Real-ESRGAN upscaling
    INPAINT= "inpaint"  # Inpainting / outpainting


class RenderStatus(str, Enum):
    PENDING    = "pending"
    ASSIGNED   = "assigned"
    RENDERING  = "rendering"
    COMPLETED  = "completed"
    FAILED     = "failed"


class RenderBackend(str, Enum):
    COMFYUI   = "comfyui"
    AUTO1111  = "auto1111"     # AUTOMATIC1111 WebUI
    DIFFUSERS = "diffusers"    # HuggingFace diffusers
    BLENDER   = "blender"
    STUB      = "stub"


@dataclass
class RenderJob:
    job_id:          str
    render_type:     RenderType
    client_address:  str
    model_id:        str           # e.g. "sdxl", "flux-schnell", "animatediff"
    prompt:          str
    negative_prompt: str   = ""
    width:           int   = 1024
    height:          int   = 1024
    steps:           int   = 20
    cfg_scale:       float = 7.0
    seed:            int   = -1    # -1 = random
    num_frames:      int   = 1     # for video/3D
    fps:             int   = 24
    max_price_bc:    float = 0.005
    min_vram_gb:     int   = 8
    extra_params:    Dict[str, Any] = field(default_factory=dict)
    status:          RenderStatus   = RenderStatus.PENDING
    worker_address:  Optional[str]  = None
    created_at:      float          = field(default_factory=time.time)

    @property
    def frame_count(self) -> int:
        return self.num_frames

    @property
    def expected_cost_bc(self) -> float:
        if self.render_type == RenderType.IMAGE:
            px = self.width * self.height
            return BC_PER_IMAGE_1024 if px >= 1024*1024 else BC_PER_IMAGE_512
        if self.render_type == RenderType.VIDEO:
            return self.num_frames * BC_PER_VIDEO_FRAME
        if self.render_type == RenderType.MODEL3D:
            return self.num_frames * BC_PER_3D_FRAME
        return BC_PER_IMAGE_512

    @classmethod
    def create(cls, render_type: RenderType, client_address: str,
               model_id: str, prompt: str, **kwargs) -> "RenderJob":
        return cls(
            job_id         = uuid.uuid4().hex,
            render_type    = render_type,
            client_address = client_address,
            model_id       = model_id,
            prompt         = prompt,
            **{k: v for k, v in kwargs.items() if hasattr(cls, k) or k in cls.__dataclass_fields__},
        )

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id, "render_type": self.render_type,
            "client_address": self.client_address, "model_id": self.model_id,
            "prompt": self.prompt, "negative_prompt": self.negative_prompt,
            "width": self.width, "height": self.height, "steps": self.steps,
            "cfg_scale": self.cfg_scale, "seed": self.seed,
            "num_frames": self.num_frames, "fps": self.fps,
            "max_price_bc": self.max_price_bc, "min_vram_gb": self.min_vram_gb,
            "extra_params": self.extra_params, "status": self.status,
            "worker_address": self.worker_address, "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "RenderJob":
        j = cls(
            job_id=d["job_id"], render_type=RenderType(d["render_type"]),
            client_address=d["client_address"], model_id=d["model_id"],
            prompt=d["prompt"], negative_prompt=d.get("negative_prompt", ""),
            width=d.get("width", 1024), height=d.get("height", 1024),
            steps=d.get("steps", 20), cfg_scale=d.get("cfg_scale", 7.0),
            seed=d.get("seed", -1), num_frames=d.get("num_frames", 1),
            fps=d.get("fps", 24), max_price_bc=d.get("max_price_bc", 0.005),
            min_vram_gb=d.get("min_vram_gb", 8),
            extra_params=d.get("extra_params", {}),
        )
        j.status = RenderStatus(d.get("status", "pending"))
        j.worker_address = d.get("worker_address")
        j.created_at = d.get("created_at", time.time())
        return j


@dataclass
class RenderResult:
    result_id:        str
    job_id:           str
    worker_address:   str
    client_address:   str
    model_id:         str
    render_type:      RenderType
    frames_rendered:  int
    width:            int
    height:           int
    output_hash:      str          # SHA256 of output bytes
    output_url:       str          # where client can fetch output
    render_time_s:    float
    price_bc:         float
    timestamp:        float = field(default_factory=time.time)
    worker_signature: Optional[str] = None

    @property
    def reward_bc(self) -> float:
        if self.render_type == RenderType.VIDEO:
            earned = self.frames_rendered * BC_PER_VIDEO_FRAME
        elif self.render_type == RenderType.MODEL3D:
            earned = self.frames_rendered * BC_PER_3D_FRAME
        else:
            px = self.width * self.height
            earned = BC_PER_IMAGE_1024 if px >= 1024*1024 else BC_PER_IMAGE_512
        return round(max(earned, self.price_bc), 8)

    def hash(self) -> str:
        content = f"{self.result_id}:{self.job_id}:{self.worker_address}:{self.output_hash}"
        return hashlib.sha256(content.encode()).hexdigest()

    def to_dict(self) -> dict:
        return {
            "result_id": self.result_id, "job_id": self.job_id,
            "worker_address": self.worker_address, "client_address": self.client_address,
            "model_id": self.model_id, "render_type": self.render_type,
            "frames_rendered": self.frames_rendered, "width": self.width,
            "height": self.height, "output_hash": self.output_hash,
            "output_url": self.output_url, "render_time_s": self.render_time_s,
            "price_bc": self.price_bc, "timestamp": self.timestamp,
            "worker_signature": self.worker_signature,
            "reward_bc": self.reward_bc,
        }
