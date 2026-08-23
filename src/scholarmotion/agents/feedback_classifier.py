from __future__ import annotations

import re

from scholarmotion.schemas import FeedbackClassification, FeedbackType

RULES = [
    (
        FeedbackType.VISUAL_DEFECT,
        "layout.overlap",
        r"overlap|collid|cover(?:s|ed)?|off[- ]?screen|clip",
    ),
    (
        FeedbackType.MATH_DEFECT,
        "math.incorrect",
        r"wrong sign|incorrect equation|math(?:ematical)? error|wrong variable",
    ),
    (FeedbackType.AUDIO_DEFECT, "audio.quality", r"audio|voice|pronounc|inaudible|noise"),
    (
        FeedbackType.TIMING_DEFECT,
        "timing.pacing",
        r"too fast|too slow|slow down|speed up|out of sync|timing",
    ),
    (FeedbackType.STYLE_PREFERENCE, "style.preference", r"color|font|style|theme"),
    (
        FeedbackType.SCOPE_CHANGE,
        "scope.change",
        r"whole video|entire video|all scenes|change the topic",
    ),
    (
        FeedbackType.SEMANTIC_DEFECT,
        "semantic.incorrect",
        r"factually|incorrect claim|does not mean|misleading",
    ),
]


def classify_feedback(instruction: str) -> list[FeedbackClassification]:
    results: list[FeedbackClassification] = []
    matched_spans: list[tuple[int, int]] = []
    for kind, category, pattern in RULES:
        match = re.search(pattern, instruction, re.IGNORECASE)
        if match:
            results.append(
                FeedbackClassification(
                    feedback_type=kind,
                    category=category,
                    instruction=instruction,
                    memory_eligible=kind
                    in {
                        FeedbackType.VISUAL_DEFECT,
                        FeedbackType.MATH_DEFECT,
                        FeedbackType.SEMANTIC_DEFECT,
                        FeedbackType.AUDIO_DEFECT,
                        FeedbackType.TIMING_DEFECT,
                    },
                )
            )
            matched_spans.append(match.span())
    content_pattern = r"change|replace|simpl(?:er|ify)|use (?:a|an)|explain .* instead"
    if re.search(content_pattern, instruction, re.IGNORECASE):
        results.insert(
            0,
            FeedbackClassification(
                feedback_type=FeedbackType.CONTENT_EDIT,
                category="content.edit",
                instruction=instruction,
                memory_eligible=False,
            ),
        )
    return results or [
        FeedbackClassification(
            feedback_type=FeedbackType.CONTENT_EDIT,
            category="content.edit",
            instruction=instruction,
            memory_eligible=False,
        )
    ]
