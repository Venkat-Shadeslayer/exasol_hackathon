from __future__ import annotations

import re

from scholarmotion.schemas import LearnerProfile, RequestType


def profile_request(
    request: str,
    *,
    target_duration_minutes: float | None = None,
    language: str = "English",
    has_paper: bool = False,
) -> LearnerProfile:
    lower = request.lower()
    known = _capture_list(
        request, r"(?:i\s+)?(?:already\s+)?understand\s+(.+?)(?:\s+but\s+|[.!]|$)"
    )
    unknown = _capture_list(request, r"(?:do not|don't|not)\s+understand\s+(.+?)(?:[.!]|$)")
    duration_match = re.search(r"(\d+(?:\.\d+)?)\s*[- ]?minute", lower)
    duration = target_duration_minutes or (float(duration_match.group(1)) if duration_match else 5)
    level_match = re.search(r"(?:class|grade)\s*(\d{1,2})", lower)
    target_level = (
        f"NCERT Class {level_match.group(1)}" if level_match or "ncert" in lower else "general"
    )
    if "equation" in lower:
        request_type = RequestType.EQUATION_EXPLANATION
    elif "figure" in lower:
        request_type = RequestType.FIGURE_EXPLANATION
    elif "section" in lower and has_paper:
        request_type = RequestType.PAPER_SECTION_EXPLANATION
    elif has_paper:
        request_type = RequestType.PAPER_EXPLANATION
    elif "compare" in lower:
        request_type = RequestType.COMPARISON
    else:
        request_type = RequestType.CONCEPT_EXPLANATION
    topic_match = re.search(
        r"explain\s+(.+?)(?:\s+(?:at|using|in|visually|for)\b|[.!]|$)", request, re.IGNORECASE
    )
    topic = topic_match.group(1).strip() if topic_match else request.strip().split(".")[0]
    return LearnerProfile(
        topic=topic,
        target_level=target_level,
        known_concepts=known,
        unknown_concepts=unknown,
        desired_duration_minutes=duration,
        language=language,
        math_depth="NCERT" if "ncert" in lower else "appropriate",
        request_type=request_type,
    )


def _capture_list(text: str, pattern: str) -> list[str]:
    match = re.search(pattern, text, re.IGNORECASE)
    if not match:
        return []
    return [
        item.strip(" ,")
        for item in re.split(r",|\band\b", match.group(1), flags=re.IGNORECASE)
        if item.strip(" ,")
    ]
