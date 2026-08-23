from __future__ import annotations

import asyncio
import json
from pathlib import Path

from scholarmotion.agents.code_generator import generate_scene_code
from scholarmotion.config import get_settings
from scholarmotion.manim_runtime.sandbox import SandboxRenderer
from scholarmotion.providers import create_llm_provider
from scholarmotion.schemas import SceneSpec, VisualBeat
from scholarmotion.verification.aggregator import aggregate_reports
from scholarmotion.verification.pedagogy import verify_pedagogy
from scholarmotion.verification.render import verify_render

CATEGORIES = [
    "equations",
    "equation transforms",
    "graphs",
    "geometry",
    "vectors",
    "particle motion",
    "tables",
    "images",
    "camera zoom",
    "subtitles",
    "multiple moving objects",
    "long derivations",
    "physics diagrams",
    "dense layouts",
]


async def run() -> None:
    settings = get_settings()
    provider = create_llm_provider(settings)
    renderer = SandboxRenderer(settings.manim_binary, settings.render_timeout_seconds)
    root = Path("evaluation/bootstrap")
    candidates: list[dict] = []
    clusters: dict[str, int] = {}
    for index, category in enumerate(CATEGORIES, 1):
        spec = SceneSpec(
            scene_id=f"E{index:02d}",
            chapter_id="EVAL",
            title=category.title(),
            learning_objective=f"Exercise the {category} visual primitive.",
            duration_target_seconds=20,
            narration=f"This golden scene evaluates recurring failures involving {category}.",
            visual_beats=[
                VisualBeat(
                    beat_id="b1",
                    narration_segment="n1",
                    visual=f"Render a minimal {category} example.",
                    primitive=category,
                )
            ],
            verification=["render succeeds", "subtitle area remains clear"],
            tags=[category],
        )
        generated = await generate_scene_code(provider, spec)
        result = renderer.render(generated.python_code, generated.scene_class, root / spec.scene_id)
        report = aggregate_reports(
            spec.scene_id,
            {
                "render": verify_render(spec.scene_id, result),
                "pedagogy": verify_pedagogy(spec),
            },
        )
        for issue in report.issues:
            clusters[issue.category] = clusters.get(issue.category, 0) + 1
        candidates.append(
            {
                "scene_id": spec.scene_id,
                "category": category,
                "passed": report.passed,
                "issues": [issue.model_dump(mode="json") for issue in report.issues],
                "promoted": False,
            }
        )
    output = Path("evaluation/correction_candidates.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps({"evaluations": candidates, "failure_clusters": clusters}, indent=2),
        encoding="utf-8",
    )
    print(
        f"Generated, rendered, and verified {len(candidates)} golden scenes. "
        f"Candidates are at {output}; one-off failures are not promoted."
    )


if __name__ == "__main__":
    asyncio.run(run())
