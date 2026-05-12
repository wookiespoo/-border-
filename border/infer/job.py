"""
BorderInfer — Job definitions

Pricing: BC per 1000 tokens (input + output).
Every completed inference = a ComputeProofRecord submitted to BorderChain.
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
BC_PER_1K_TOKENS_INPUT  = 0.0002   # cheaper than OpenAI GPT-4
BC_PER_1K_TOKENS_OUTPUT = 0.0008
BC_PER_EMBEDDING        = 0.00001  # per embedding call
MIN_STAKE_TO_INFER      = 5.0      # BC worker must stake to accept jobs


class InferType(str, Enum):
    CHAT        = "chat"         # conversational / instruction
    COMPLETION  = "completion"   # raw text completion
    EMBEDDING   = "embedding"    # vector embedding
    CLASSIFY    = "classify"     # text classification
    SUMMARISE   = "summarise"    # summarisation


class InferStatus(str, Enum):
    PENDING    = "pending"
    ASSIGNED   = "assigned"
    STREAMING  = "streaming"
    COMPLETED  = "completed"
    FAILED     = "failed"


@dataclass
class InferJob:
    job_id:          str
    infer_type:      InferType
    client_address:  str
    model_id:        str           # e.g. "llama3:8b", "mistral:7b", "nomic-embed-text"
    messages:        List[Dict]    # OpenAI-style [{role, content}]
    max_tokens:      int   = 512
    temperature:     float = 0.7
    max_price_bc:    float = 0.01  # max client will pay
    min_vram_gb:     int   = 0
    status:          InferStatus   = InferStatus.PENDING
    worker_address:  Optional[str] = None
    created_at:      float         = field(default_factory=time.time)

    @classmethod
    def create(cls, infer_type: InferType, client_address: str,
               model_id: str, messages: List[Dict],
               max_tokens: int = 512, temperature: float = 0.7,
               max_price_bc: float = 0.01, min_vram_gb: int = 0) -> "InferJob":
        return cls(
            job_id         = uuid.uuid4().hex,
            infer_type     = infer_type,
            client_address = client_address,
            model_id       = model_id,
            messages       = messages,
            max_tokens     = max_tokens,
            temperature    = temperature,
            max_price_bc   = max_price_bc,
            min_vram_gb    = min_vram_gb,
        )

    def input_text(self) -> str:
        return " ".join(m.get("content", "") for m in self.messages)

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id, "infer_type": self.infer_type,
            "client_address": self.client_address, "model_id": self.model_id,
            "messages": self.messages, "max_tokens": self.max_tokens,
            "temperature": self.temperature, "max_price_bc": self.max_price_bc,
            "min_vram_gb": self.min_vram_gb, "status": self.status,
            "worker_address": self.worker_address, "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "InferJob":
        j = cls(
            job_id=d["job_id"], infer_type=InferType(d["infer_type"]),
            client_address=d["client_address"], model_id=d["model_id"],
            messages=d["messages"], max_tokens=d.get("max_tokens", 512),
            temperature=d.get("temperature", 0.7),
            max_price_bc=d.get("max_price_bc", 0.01),
            min_vram_gb=d.get("min_vram_gb", 0),
        )
        j.status = InferStatus(d.get("status", "pending"))
        j.worker_address = d.get("worker_address")
        j.created_at = d.get("created_at", time.time())
        return j


@dataclass
class InferResult:
    result_id:       str
    job_id:          str
    worker_address:  str
    client_address:  str
    model_id:        str
    input_tokens:    int
    output_tokens:   int
    output_text:     str
    embeddings:      Optional[List[float]]
    latency_ms:      float
    price_bc:        float
    timestamp:       float = field(default_factory=time.time)
    worker_signature: Optional[str] = None

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def reward_bc(self) -> float:
        in_cost  = (self.input_tokens  / 1000) * BC_PER_1K_TOKENS_INPUT
        out_cost = (self.output_tokens / 1000) * BC_PER_1K_TOKENS_OUTPUT
        return round(max(in_cost + out_cost, self.price_bc), 8)

    def hash(self) -> str:
        content = f"{self.result_id}:{self.job_id}:{self.worker_address}:{self.output_tokens}:{self.timestamp}"
        return hashlib.sha256(content.encode()).hexdigest()

    def input_hash(self) -> str:
        return hashlib.sha256(json.dumps({"job_id": self.job_id}, sort_keys=True).encode()).hexdigest()

    def output_hash(self) -> str:
        return hashlib.sha256(self.output_text.encode()).hexdigest()

    def to_dict(self) -> dict:
        return {
            "result_id": self.result_id, "job_id": self.job_id,
            "worker_address": self.worker_address, "client_address": self.client_address,
            "model_id": self.model_id, "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens, "output_text": self.output_text,
            "embeddings": self.embeddings, "latency_ms": self.latency_ms,
            "price_bc": self.price_bc, "timestamp": self.timestamp,
            "worker_signature": self.worker_signature,
            "reward_bc": self.reward_bc,
        }
