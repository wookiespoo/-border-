"""
BorderRender — Decentralised GPU rendering on the Border network.

Image · Video · 3D — pay BC per frame.
Your GPU renders. You earn. No Midjourney. No Runway. No cloud.

Usage:
    from border.render import RenderJob, RenderType, RenderMarket, RenderDaemon

    job = RenderJob.create(
        render_type=RenderType.IMAGE,
        client_address="BC_...",
        model_id="sdxl",
        prompt="A futuristic city at dusk, cyberpunk",
        width=1024, height=1024, steps=30,
        max_price_bc=0.003,
    )
    market.submit_job(job)
"""

from .job    import (RenderJob, RenderResult, RenderType, RenderStatus, RenderBackend,
                     BC_PER_IMAGE_512, BC_PER_IMAGE_1024,
                     BC_PER_VIDEO_FRAME, BC_PER_3D_FRAME, MIN_STAKE_TO_RENDER)
from .market import RenderMarket, RenderWorker
from .node   import BorderRenderNode, serve_render
from .daemon import RenderDaemon

__all__ = [
    "RenderJob", "RenderResult", "RenderType", "RenderStatus", "RenderBackend",
    "BC_PER_IMAGE_512", "BC_PER_IMAGE_1024", "BC_PER_VIDEO_FRAME",
    "BC_PER_3D_FRAME", "MIN_STAKE_TO_RENDER",
    "RenderMarket", "RenderWorker",
    "BorderRenderNode", "serve_render",
    "RenderDaemon",
]
