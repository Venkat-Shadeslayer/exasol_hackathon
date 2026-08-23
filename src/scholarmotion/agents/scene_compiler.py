from __future__ import annotations

from scholarmotion.schemas import NarrationBlock, SceneSpec, VisualBeat


def compile_scene_specs(
    blocks: list[NarrationBlock], storyboard: dict[str, list[VisualBeat]]
) -> list[SceneSpec]:
    specs: list[SceneSpec] = []
    for index, block in enumerate(blocks, 1):
        beats = storyboard[block.block_id]
        duration = max(20, min(60, block.estimated_duration_seconds))
        beat_duration = duration / len(beats)
        timed = [
            beat.model_copy(
                update={"start_seconds": i * beat_duration, "end_seconds": (i + 1) * beat_duration}
            )
            for i, beat in enumerate(beats)
        ]
        specs.append(
            SceneSpec(
                scene_id=f"S{index:02d}",
                chapter_id=block.chapter_id,
                title=block.learning_objective.removesuffix("."),
                learning_objective=block.learning_objective,
                duration_target_seconds=duration,
                narration=block.text,
                visual_beats=timed,
                source_ids=block.source_ids,
                start_visual_state=[],
                end_visual_state=["recap visible"],
                verification=[
                    "no overlap",
                    "subtitle safe area remains clear",
                    "objective is visibly demonstrated",
                ],
                tags=sorted({beat.primitive.lower() for beat in beats}),
            )
        )
    return specs
