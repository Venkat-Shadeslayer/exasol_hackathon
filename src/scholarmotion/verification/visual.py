from __future__ import annotations

from pathlib import Path

from scholarmotion.schemas import SceneSpec, VerificationIssue


async def verify_visual(
    provider, spec: SceneSpec, frame_paths: list[str]
) -> list[VerificationIssue]:
    if not getattr(provider, "capabilities", None) or not provider.capabilities.images:
        return []
    images = [Path(path).read_bytes() for path in frame_paths if Path(path).exists()]
    result = await provider.analyze_images(
        "Check composition, readability, state, arrows, motion, and narration alignment. "
        + spec.model_dump_json(),
        images,
    )
    return [
        VerificationIssue(scene_id=spec.scene_id, **issue) for issue in result.get("issues", [])
    ]
