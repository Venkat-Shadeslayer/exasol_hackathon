from __future__ import annotations

from scholarmotion.manim_runtime.instrumentation import ObjectBounds
from scholarmotion.schemas import VerificationIssue


def verify_layout(
    scene_id: str,
    objects: list[ObjectBounds],
    *,
    frame=(-7.0, 7.0, -4.0, 4.0),
    subtitle_top: float = -3.0,
    min_text_height: float = 0.22,
) -> list[VerificationIssue]:
    issues: list[VerificationIssue] = []
    left, right, bottom, top = frame
    for obj in objects:
        if obj.left < left or obj.right > right or obj.bottom < bottom or obj.top > top:
            issues.append(
                VerificationIssue(
                    scene_id=scene_id,
                    category="layout.out_of_frame",
                    severity="high",
                    confidence=1,
                    objects=[obj.name],
                    description=f"{obj.name} leaves the safe frame.",
                    suggested_repair=(
                        f"The {obj.name} object (bounds x=[{obj.left:.2f}, {obj.right:.2f}], "
                        f"y=[{obj.bottom:.2f}, {obj.top:.2f}]) leaves the safe frame "
                        f"(x in [{left}, {right}], y in [{bottom}, {top}]). Reposition it inside "
                        "the frame and call keep_inside_frame() on it, or scale it down."
                    ),
                    correction_memory_candidate=True,
                )
            )
        if obj.text_height is not None and obj.text_height < min_text_height:
            issues.append(
                VerificationIssue(
                    scene_id=scene_id,
                    category="layout.text_too_small",
                    severity="medium",
                    confidence=1,
                    objects=[obj.name],
                    description=f"{obj.name} text is too small.",
                    suggested_repair=(
                        f"The {obj.name} text object has height {obj.text_height:.3f}, below the "
                        f"minimum readable height {min_text_height}. Increase its font_size."
                    ),
                    correction_memory_candidate=True,
                )
            )
        if obj.reserved_region != "subtitles" and obj.bottom < subtitle_top:
            issues.append(
                VerificationIssue(
                    scene_id=scene_id,
                    category="layout.subtitle_collision",
                    severity="high",
                    confidence=0.98,
                    objects=[obj.name],
                    description=f"{obj.name} enters the subtitle safe area.",
                    suggested_repair=(
                        f"The {obj.name} object extends down to y={obj.bottom:.2f}, below the "
                        f"subtitle-safe boundary y={subtitle_top}. Move it up so it stays above "
                        "SubtitleSafeRegion."
                    ),
                    correction_memory_candidate=True,
                )
            )
    for index, first in enumerate(objects):
        for second in objects[index + 1 :]:
            same_explicit_region = (
                first.reserved_region is not None
                and first.reserved_region == second.reserved_region
            )
            if first.overlaps(second) and not same_explicit_region:
                issues.append(
                    VerificationIssue(
                        scene_id=scene_id,
                        category="layout.overlap",
                        severity="high",
                        confidence=0.97,
                        objects=[first.name, second.name],
                        description=f"{first.name} overlaps {second.name}.",
                        suggested_repair=(
                            f"The {first.name} object overlaps the {second.name} object. "
                            "Reserve non-overlapping layout regions before scaling, and call "
                            "avoid_overlap(first, second) between them."
                        ),
                        correction_memory_candidate=True,
                    )
                )
    return issues
