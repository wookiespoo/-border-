"""
BorderInfer — Model Registry

Tracks which models a worker has loaded and what they can serve.
In production: checks ollama `GET /api/tags` or llama.cpp health endpoint.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class ModelBackend(str, Enum):
    OLLAMA    = "ollama"     # ollama serve
    LLAMA_CPP = "llama_cpp"  # llama.cpp server
    STUB      = "stub"       # in-process stub (demo/test)


@dataclass
class ModelSpec:
    model_id:      str            # e.g. "llama3:8b"
    backend:       ModelBackend
    vram_gb:       float
    context_len:   int            # max context tokens
    tokens_per_s:  float          # benchmark throughput
    supports_embed: bool = False
    endpoint:      str   = ""     # e.g. "http://localhost:11434"
    loaded_at:     float = field(default_factory=time.time)

    @property
    def is_embed_model(self) -> bool:
        return self.supports_embed or "embed" in self.model_id.lower()

    def to_dict(self) -> dict:
        return {
            "model_id": self.model_id, "backend": self.backend,
            "vram_gb": self.vram_gb, "context_len": self.context_len,
            "tokens_per_s": self.tokens_per_s, "supports_embed": self.supports_embed,
            "endpoint": self.endpoint,
        }


class ModelRegistry:
    """
    Per-worker registry of available models.
    Workers advertise their loaded models when registering with the market.
    """

    # Well-known defaults (what a typical Border node might load)
    PRESETS: Dict[str, dict] = {
        "llama3:8b":          {"vram_gb": 6.0,  "context_len": 8192,  "tokens_per_s": 45.0},
        "llama3:70b":         {"vram_gb": 40.0, "context_len": 8192,  "tokens_per_s": 12.0},
        "mistral:7b":         {"vram_gb": 5.5,  "context_len": 32768, "tokens_per_s": 50.0},
        "mixtral:8x7b":       {"vram_gb": 26.0, "context_len": 32768, "tokens_per_s": 18.0},
        "phi3:mini":          {"vram_gb": 2.5,  "context_len": 4096,  "tokens_per_s": 80.0},
        "nomic-embed-text":   {"vram_gb": 0.5,  "context_len": 8192,  "tokens_per_s": 500.0, "supports_embed": True},
        "mxbai-embed-large":  {"vram_gb": 0.7,  "context_len": 512,   "tokens_per_s": 400.0, "supports_embed": True},
        "sdxl":               {"vram_gb": 8.0,  "context_len": 77,    "tokens_per_s": 2.0},   # image model
    }

    def __init__(self):
        self._models: Dict[str, ModelSpec] = {}

    def register(self, model_id: str,
                 backend: ModelBackend = ModelBackend.OLLAMA,
                 endpoint: str = "http://localhost:11434",
                 **overrides) -> ModelSpec:
        preset = self.PRESETS.get(model_id, {})
        spec = ModelSpec(
            model_id     = model_id,
            backend      = backend,
            vram_gb      = overrides.get("vram_gb",      preset.get("vram_gb", 4.0)),
            context_len  = overrides.get("context_len",  preset.get("context_len", 4096)),
            tokens_per_s = overrides.get("tokens_per_s", preset.get("tokens_per_s", 30.0)),
            supports_embed=overrides.get("supports_embed", preset.get("supports_embed", False)),
            endpoint     = endpoint,
        )
        self._models[model_id] = spec
        return spec

    def get(self, model_id: str) -> Optional[ModelSpec]:
        return self._models.get(model_id)

    def all(self) -> List[ModelSpec]:
        return list(self._models.values())

    def can_serve(self, model_id: str, available_vram_gb: float) -> bool:
        spec = self.get(model_id)
        return spec is not None and spec.vram_gb <= available_vram_gb

    def embed_models(self) -> List[ModelSpec]:
        return [m for m in self._models.values() if m.is_embed_model]

    def to_list(self) -> List[dict]:
        return [m.to_dict() for m in self._models.values()]
