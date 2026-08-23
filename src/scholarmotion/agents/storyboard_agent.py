from __future__ import annotations

from scholarmotion.schemas import NarrationBlock, VisualBeat


def create_storyboard(blocks: list[NarrationBlock]) -> dict[str, list[VisualBeat]]:
    storyboard: dict[str, list[VisualBeat]] = {}
    for block in blocks:
        storyboard[block.block_id] = [
            VisualBeat(
                beat_id=f"{block.block_id}-b1",
                narration_segment=block.block_id,
                visual="Introduce one labeled visual model.",
                primitive="diagram",
            ),
            VisualBeat(
                beat_id=f"{block.block_id}-b2",
                narration_segment=block.block_id,
                visual="Animate the relevant transformation and highlight what stays invariant.",
                primitive="transformation",
            ),
            VisualBeat(
                beat_id=f"{block.block_id}-b3",
                narration_segment=block.block_id,
                visual="Recap with a compact equation or label, keeping subtitles clear.",
                primitive="MathTex",
            ),
        ]
    return storyboard
