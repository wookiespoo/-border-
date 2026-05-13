"""
BorderCompute — Job + Proof dataclasses
========================================
The atomic units of the BorderCompute network.

ComputeJob  : A unit of work submitted by a client (inference, render, train)
ComputeProof: Cryptographic proof that a worker completed a job — submitted to the chain
GPUSpec     : Hardware descriptor for a worker node
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


# ─────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────

BC_PER_COMPUTE_HOUR = 2.0       # BC earned per GPU-hour of compute
BC_PER_GB_PROCESSED = 0.1       # BC earned per GB of data processed
MIN_STAKE_TO_WORK   = 1.0       # Minimum BC staked to accept jobs
JOB_TIMEOUT_SECONDS = 300       # Job expires if not completed in 5 min


# ─────────────────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────────────────

class JobType(str, Enum):
    INFERENCE = "INFERENCE"     # Run AI model inference
    RENDER    = "RENDER"        # Image / video generation
    TRAIN     = "TRAIN"         # Fine-tune / LoRA training
    CUSTOM    = "CUSTOM"        # Arbitrary compute script

class JobStatus(str, Enum):
    PENDING   = "PENDING"       # Waiting for a worker
    ASSIGNED  = "ASSIGNED"      # Worker accepted, running
    COMPLETED = "COMPLETED"     # Result ready, proof submitted
    FAILED    = "FAILED"        # Worker failed or timed out
    EXPIRED   = "EXPIRED"       # Nobody picked it up in time

class ComputeType(str, Enum):
    CUDA   = "CUDA"             # NVIDIA
    ROCM   = "ROCM"             # AMD
    OPENCL = "OPENCL"           # Generic


# ─────────────────────────────────────────────────────────
# GPU Spec
# ─────────────────────────────────────────────────────────

@dataclass
class GPUSpec:
    """Hardware descriptor for a single GPU on a worker node."""
    gpu_id:       str            # Unique ID within the worker
    name:         str            # e.g. "RX 580", "RTX 3080"
    vram_gb:      int            # VRAM in gigabytes
    compute_type: ComputeType    # CUDA / ROCm / OpenCL

    def to_dict(self) -> dict:
        return {
            "gpu_id":       self.gpu_id,
            "name":         self.name,
            "vram_gb":      self.vram_gb,
            "compute_type": self.compute_type,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "GPUSpec":
        return cls(
            gpu_id=d["gpu_id"],
            name=d["name"],
            vram_gb=d["vram_gb"],
            compute_type=ComputeType(d["compute_type"]),
        )

    @classmethod
    def detect(cls) -> List["GPUSpec"]:
        """
        Auto-detect GPUs on this machine.
        Falls back to a single CPU-mode entry if no GPU libraries found.
        """
        specs = []

        # Try NVIDIA via pynvml
        try:
            import pynvml
            pynvml.nvmlInit()
            count = pynvml.nvmlDeviceGetCount()
            for i in range(count):
                handle = pynvml.nvmlDeviceGetHandleByIndex(i)
                name   = pynvml.nvmlDeviceGetName(handle)
                mem    = pynvml.nvmlDeviceGetMemoryInfo(handle)
                vram   = mem.total // (1024 ** 3)
                if isinstance(name, bytes):
                    name = name.decode()
                specs.append(cls(
                    gpu_id=f"nvidia_{i}",
                    name=name,
                    vram_gb=vram,
                    compute_type=ComputeType.CUDA,
                ))
        except Exception:
            pass

        # Try AMD via ROCm sysfs
        if not specs:
            try:
                import os
                rocm_path = "/sys/class/drm"
                if os.path.exists(rocm_path):
                    for i, d in enumerate(sorted(os.listdir(rocm_path))):
                        if d.startswith("card") and not d.endswith("-"):
                            vram_path = f"{rocm_path}/{d}/device/mem_info_vram_total"
                            if os.path.exists(vram_path):
                                vram_bytes = int(open(vram_path).read().strip())
                                specs.append(cls(
                                    gpu_id=f"amd_{i}",
                                    name=f"AMD GPU {i}",
                                    vram_gb=vram_bytes // (1024 ** 3),
                                    compute_type=ComputeType.ROCM,
                                ))
            except Exception:
                pass

        # CPU fallback
        if not specs:
            specs.append(cls(
                gpu_id="cpu_0",
                name="CPU (no GPU detected)",
                vram_gb=0,
                compute_type=ComputeType.OPENCL,
            ))

        return specs


# ─────────────────────────────────────────────────────────
# Compute Job
# ─────────────────────────────────────────────────────────

@dataclass
class ComputeJob:
    """
    A unit of work submitted by a client to the BorderCompute network.
    Workers pick up jobs that match their GPU capabilities.
    """
    job_id:          str
    job_type:        JobType
    client_address:  str            # BC wallet address of submitter
    model_id:        str            # Model/task identifier
    input_data:      Dict[str, Any] # Job payload
    max_price_bc:    float          # Max BC the client will pay
    min_vram_gb:     int            # Minimum GPU VRAM required
    submitted_at:    float          = field(default_factory=time.time)
    status:          JobStatus      = JobStatus.PENDING
    worker_address:  Optional[str]  = None
    assigned_at:     Optional[float]= None
    completed_at:    Optional[float]= None
    result:          Optional[Dict] = None
    error:           Optional[str]  = None

    @classmethod
    def create(
        cls,
        job_type:       JobType,
        client_address: str,
        model_id:       str,
        input_data:     dict,
        max_price_bc:   float = 0.01,
        min_vram_gb:    int   = 4,
    ) -> "ComputeJob":
        return cls(
            job_id=f"job_{uuid.uuid4().hex[:12]}",
            job_type=job_type,
            client_address=client_address,
            model_id=model_id,
            input_data=input_data,
            max_price_bc=max_price_bc,
            min_vram_gb=min_vram_gb,
        )

    def is_expired(self) -> bool:
        return (
            self.status == JobStatus.PENDING
            and time.time() - self.submitted_at > JOB_TIMEOUT_SECONDS
        )

    def input_hash(self) -> str:
        """Deterministic hash of job input (for proof verification)."""
        content = json.dumps(self.input_data, sort_keys=True)
        return hashlib.sha256(content.encode()).hexdigest()

    def to_dict(self) -> dict:
        return {
            "job_id":         self.job_id,
            "job_type":       self.job_type,
            "client_address": self.client_address,
            "model_id":       self.model_id,
            "input_data":     self.input_data,
            "max_price_bc":   self.max_price_bc,
            "min_vram_gb":    self.min_vram_gb,
            "submitted_at":   self.submitted_at,
            "status":         self.status,
            "worker_address": self.worker_address,
            "assigned_at":    self.assigned_at,
            "completed_at":   self.completed_at,
            "result":         self.result,
            "error":          self.error,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ComputeJob":
        return cls(
            job_id=d["job_id"],
            job_type=JobType(d["job_type"]),
            client_address=d["client_address"],
            model_id=d["model_id"],
            input_data=d["input_data"],
            max_price_bc=d["max_price_bc"],
            min_vram_gb=d["min_vram_gb"],
            submitted_at=d["submitted_at"],
            status=JobStatus(d["status"]),
            worker_address=d.get("worker_address"),
            assigned_at=d.get("assigned_at"),
            completed_at=d.get("completed_at"),
            result=d.get("result"),
            error=d.get("error"),
        )


# ─────────────────────────────────────────────────────────
# Compute Proof
# ─────────────────────────────────────────────────────────

@dataclass
class ComputeProof:
    """
    Cryptographic proof that a worker completed a compute job.
    Submitted to the BorderChain — mints BorderCoin for the worker.

    Analogous to BandwidthProof but for GPU work instead of network traffic.
    """
    proof_id:         str
    job_id:           str
    job_type:         JobType
    worker_address:   str           # BC wallet address of the GPU operator
    client_address:   str           # BC wallet address of the job submitter
    model_id:         str
    compute_seconds:  float         # Wall-clock time the GPU spent on the job
    gpu_name:         str           # Which GPU ran it
    vram_used_gb:     int
    bytes_processed:  int           # Input + output data size
    input_hash:       str           # SHA256 of input_data (verifiable)
    output_hash:      str           # SHA256 of result
    timestamp:        float         = field(default_factory=time.time)
    worker_signature:  str          = ""
    worker_public_key: str          = ""   # Ed25519 public key (base64) for signature verification
    price_bc:          float         = 0.0  # Agreed price

    @classmethod
    def from_job(
        cls,
        job:          "ComputeJob",
        worker_address: str,
        gpu_spec:     GPUSpec,
        compute_seconds: float,
        output_hash:  str,
        price_bc:     float,
    ) -> "ComputeProof":
        input_bytes  = len(json.dumps(job.input_data).encode())
        output_bytes = len(output_hash)  # proxy for result size
        return cls(
            proof_id=f"cproof_{uuid.uuid4().hex[:12]}",
            job_id=job.job_id,
            job_type=job.job_type,
            worker_address=worker_address,
            client_address=job.client_address,
            model_id=job.model_id,
            compute_seconds=compute_seconds,
            gpu_name=gpu_spec.name,
            vram_used_gb=gpu_spec.vram_gb,
            bytes_processed=input_bytes + output_bytes,
            input_hash=job.input_hash(),
            output_hash=output_hash,
            price_bc=price_bc,
        )

    def compute_reward_bc(self) -> float:
        """
        BC earned by the worker for this proof.
        Based on GPU-hours + data processed.
        """
        hours = self.compute_seconds / 3600
        gb    = self.bytes_processed / (1024 ** 3)
        time_reward = hours * BC_PER_COMPUTE_HOUR
        data_reward = gb   * BC_PER_GB_PROCESSED
        return round(max(time_reward + data_reward, self.price_bc), 8)

    def hash(self) -> str:
        content = (
            f"{self.job_id}:{self.worker_address}:{self.client_address}:"
            f"{self.compute_seconds}:{self.input_hash}:{self.output_hash}:{self.timestamp}"
        )
        return hashlib.sha256(content.encode()).hexdigest()

    def verify_signature(self) -> bool:
        """Verify worker_signature is a valid Ed25519 sig over hash()."""
        if not self.worker_signature or not self.worker_public_key:
            return False
        try:
            import base64
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
            pub_bytes = base64.b64decode(self.worker_public_key)
            pub_key   = Ed25519PublicKey.from_public_bytes(pub_bytes)
            sig_bytes = base64.b64decode(self.worker_signature)
            pub_key.verify(sig_bytes, self.hash().encode())
            return True
        except Exception:
            return False

    def to_dict(self) -> dict:
        return {
            "proof_id":         self.proof_id,
            "job_id":           self.job_id,
            "job_type":         self.job_type,
            "worker_address":   self.worker_address,
            "client_address":   self.client_address,
            "model_id":         self.model_id,
            "compute_seconds":  self.compute_seconds,
            "gpu_name":         self.gpu_name,
            "vram_used_gb":     self.vram_used_gb,
            "bytes_processed":  self.bytes_processed,
            "input_hash":       self.input_hash,
            "output_hash":      self.output_hash,
            "timestamp":        self.timestamp,
            "worker_signature":  self.worker_signature,
            "worker_public_key": self.worker_public_key,
            "price_bc":         self.price_bc,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ComputeProof":
        return cls(
            proof_id=d["proof_id"],
            job_id=d["job_id"],
            job_type=JobType(d["job_type"]),
            worker_address=d["worker_address"],
            client_address=d["client_address"],
            model_id=d["model_id"],
            compute_seconds=d["compute_seconds"],
            gpu_name=d["gpu_name"],
            vram_used_gb=d["vram_used_gb"],
            bytes_processed=d["bytes_processed"],
            input_hash=d["input_hash"],
            output_hash=d["output_hash"],
            timestamp=d["timestamp"],
            worker_signature=d.get("worker_signature", ""),
            worker_public_key=d.get("worker_public_key", ""),
            price_bc=d.get("price_bc", 0.0),
        )
