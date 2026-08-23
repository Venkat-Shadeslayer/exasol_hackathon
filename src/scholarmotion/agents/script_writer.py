from __future__ import annotations

import math

from scholarmotion.media.speech import to_speakable
from scholarmotion.schemas import CurriculumItem, CurriculumPlan, NarrationBlock

RULES = """You are writing spoken narration for one segment of a physics teaching video.
Write natural spoken English: no headers, no bullet points, no stage directions.
State the actual physics — the definition, the equation, the reasoning, a concrete example —
using the supplied textbook facts as grounding. Do not describe the teaching process itself
(never write meta-commentary like "we will explore this" or "notice what changes on screen");
just teach the concept directly, as a person would explain it out loud.
This narration is read aloud by a speech engine and shown as subtitles, so write it as plain
spoken words only. Never use LaTeX or any math markup: no $...$, no backslash commands, no
^ or _ for powers and subscripts. Say "p equals q times two a", "one over r cubed",
"epsilon naught" — spell the mathematics out the way you would say it in a lecture.
Return ONLY the narration text for this segment, nothing else."""


def _fallback_narration(item: CurriculumItem, *, target_words: int, is_closing: bool) -> str:
    """Deterministic narration used verbatim by the mock provider, and offered to real
    providers as a baseline they are free to improve on using the grounding facts."""
    sentences = [
        f"{item.title}. {item.objective}",
        f"This concept is defined precisely so that every symbol used with {item.title} has a "
        "fixed, unambiguous meaning.",
        "Consider a concrete example: work through the defining relationship step by step and "
        "check that each quantity behaves the way the definition predicts.",
    ]
    if is_closing:
        sentences.append(f"The key takeaway is the exact relationship that defines {item.title}.")
    text = " ".join(sentences)
    words = text.split()
    if len(words) < target_words:
        text = text + " " + " ".join(sentences[1:]) * max(0, (target_words - len(words)) // 15)
    return text.strip()


def _build_prompt(
    item: CurriculumItem,
    evidence: list[dict],
    *,
    target_seconds: float,
    is_opening: bool,
    is_closing: bool,
) -> str:
    target_words = max(35, round(target_seconds * 140 / 60))
    facts = "\n".join(
        f"- ({card.get('kind', 'fact')}) {card.get('content', '')}" for card in evidence[:8]
    )
    position = (
        "This is the opening segment of the video."
        if is_opening
        else "This continues directly from the previous segment; do not re-introduce the topic."
    )
    closing = " End with a one-sentence takeaway the learner should remember." if is_closing else ""
    fallback = _fallback_narration(item, target_words=target_words, is_closing=is_closing)
    return (
        f"{RULES}\n\n"
        f"Segment subject: {item.title}\n"
        f"Learning objective: {item.objective}\n"
        f"{position}{closing}\n"
        f"Target length: about {target_words} words.\n"
        f"Relevant textbook facts to ground the explanation in:\n{facts or '(none retrieved)'}\n\n"
        "Write a substantially better, more specific narration than the placeholder below, "
        "grounded in the facts above — do not just return it unchanged.\n\n"
        f"TASK:{fallback}"
    )


async def write_script(provider, curriculum: CurriculumPlan, dossier: dict) -> list[NarrationBlock]:
    """Outline -> per-segment LLM narration grounded in retrieved evidence."""
    evidence = dossier.get("evidence", [])
    blocks: list[NarrationBlock] = []
    for chapter_index, item in enumerate(curriculum.items, 1):
        scene_count = max(1, math.ceil(item.duration_seconds / 45))
        duration = item.duration_seconds / scene_count
        for part in range(1, scene_count + 1):
            prompt = _build_prompt(
                item,
                evidence,
                target_seconds=duration,
                is_opening=not blocks,
                is_closing=(chapter_index == len(curriculum.items) and part == scene_count),
            )
            # Models reach for LaTeX even when told not to, and this same string
            # is both spoken and subtitled, so normalise it rather than trust it.
            text = to_speakable((await provider.generate_text(prompt)).strip())
            if not text:
                text = f"{item.title}. {item.objective}"
            blocks.append(
                NarrationBlock(
                    block_id=f"N{len(blocks) + 1:02d}",
                    chapter_id=f"C{chapter_index:02d}",
                    text=text,
                    learning_objective=item.objective,
                    source_ids=item.source_ids,
                    estimated_duration_seconds=duration,
                    defined_symbols={},
                )
            )
    return blocks


REVISION_RULES = """You are revising the spoken narration for one segment of a physics teaching video,
because a student said they did not understand this part.

Rewrite the narration so it addresses their request. Keep the same physics and the same learning
objective, but change how it is explained. Stay roughly the same length unless the request implies
otherwise, for example asking for a slower or more detailed explanation.

Write natural spoken English only. Never acknowledge the request, never say "as you asked" or
"let me explain again", and never mention that this is a revision. The student should simply hear a
better explanation. Never use LaTeX or math markup: no $...$, no backslash commands, no ^ or _.
Spell mathematics out the way you would say it aloud.
Return ONLY the revised narration text, nothing else."""


async def revise_narration(
    provider,
    narration: str,
    instruction: str,
    *,
    learning_objective: str = "",
) -> str:
    """Rewrite narration to satisfy a student's clarification request.

    The naive alternative -- appending the instruction to the narration -- makes the
    speech engine read the request itself out loud, so the learner hears
    "Edit requested: explain this more slowly" instead of a better explanation.
    """
    prompt = (
        f"{REVISION_RULES}\n\n"
        f"Learning objective: {learning_objective or '(unchanged)'}\n\n"
        f"STUDENT'S REQUEST:\n{instruction}\n\n"
        f"CURRENT NARRATION:\n{narration}\n\n"
        "REVISED NARRATION:"
    )
    try:
        revised = (await provider.generate_text(prompt)).strip()
    except Exception:
        # A failed revision must not lose the lesson; keep what already worked.
        return narration
    return to_speakable(revised) if revised else narration
