from __future__ import annotations

from scholarmotion.schemas import SceneSpec, VerificationIssue


def verify_pedagogy(spec: SceneSpec) -> list[VerificationIssue]:
    issues: list[VerificationIssue] = []
    if not spec.learning_objective.strip():
        issues.append(
            VerificationIssue(
                scene_id=spec.scene_id,
                category="pedagogy.missing_objective",
                severity="high",
                confidence=1,
                description="Scene has no learning objective.",
                suggested_repair="Revise curriculum and SceneSpec.",
                correction_memory_candidate=False,
            )
        )
    if len(spec.visual_beats) > max(8, spec.duration_target_seconds / 4):
        issues.append(
            VerificationIssue(
                scene_id=spec.scene_id,
                category="pedagogy.cognitive_load",
                severity="medium",
                confidence=0.9,
                description="Too many visual beats for the target duration.",
                suggested_repair="Split or simplify the scene.",
                correction_memory_candidate=False,
            )
        )
    if not spec.narration.strip():
        issues.append(
            VerificationIssue(
                scene_id=spec.scene_id,
                category="pedagogy.missing_narration",
                severity="critical",
                confidence=1,
                description="Narration is empty.",
                suggested_repair="Regenerate the narration block.",
                correction_memory_candidate=False,
            )
        )
    return issues
