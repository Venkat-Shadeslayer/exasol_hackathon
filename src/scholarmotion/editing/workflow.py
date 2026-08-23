from __future__ import annotations

from dataclasses import dataclass, field, replace
from hashlib import sha256

from scholarmotion.agents.feedback_classifier import classify_feedback
from scholarmotion.editing.dependencies import invalidation_for
from scholarmotion.media.timeline import build_timeline, resolve_scenes
from scholarmotion.schemas import Timeline


@dataclass(frozen=True)
class EditableScene:
    scene_id: str
    duration: float
    spec_version: int = 1
    code_version: int = 1
    audio_version: int = 1
    render_version: int = 1
    narration: str = ""
    render_bytes: bytes = b"render-v1"
    history: tuple[dict, ...] = ()

    @property
    def digest(self) -> str:
        return sha256(self.render_bytes).hexdigest()


@dataclass
class EditableProject:
    project_id: str
    scenes: list[EditableScene]
    assembly_inputs: list[tuple[str, int]] = field(default_factory=list)
    timeline: Timeline | None = None

    def rebuild_timeline(self) -> Timeline:
        self.timeline = build_timeline(
            [(scene.scene_id, scene.duration, scene.render_version, None) for scene in self.scenes]
        )
        return self.timeline


class SelectiveEditor:
    """Pure domain workflow used by the DB service and selective-regeneration tests."""

    def edit_range(
        self, project: EditableProject, start: float, end: float, instruction: str
    ) -> list[str]:
        timeline = project.timeline or project.rebuild_timeline()
        affected_ids = {segment.scene_id for segment in resolve_scenes(timeline, start, end)}
        classification = classify_feedback(instruction)[0]
        plan = invalidation_for(classification.feedback_type, instruction)
        updated: list[EditableScene] = []
        for scene in project.scenes:
            if scene.scene_id not in affected_ids:
                updated.append(scene)
                continue
            snapshot = {
                "spec_version": scene.spec_version,
                "code_version": scene.code_version,
                "audio_version": scene.audio_version,
                "render_version": scene.render_version,
                "narration": scene.narration,
                "render_bytes": scene.render_bytes.hex(),
            }
            spec_increment = (
                1
                if plan.root.value in {"profile", "pedagogy", "script", "storyboard", "spec"}
                else 0
            )
            narration = scene.narration + (
                f" [Edit: {instruction}]" if plan.regenerate_audio else ""
            )
            updated.append(
                replace(
                    scene,
                    spec_version=scene.spec_version + spec_increment,
                    code_version=scene.code_version + 1,
                    audio_version=scene.audio_version + (1 if plan.regenerate_audio else 0),
                    render_version=scene.render_version + 1,
                    narration=narration,
                    render_bytes=f"{scene.scene_id}|render-v{scene.render_version + 1}|{instruction}".encode(),
                    history=scene.history + (snapshot,),
                )
            )
        project.scenes = updated
        project.assembly_inputs = [
            (scene.scene_id, scene.render_version) for scene in project.scenes
        ]
        project.rebuild_timeline()
        return [scene.scene_id for scene in project.scenes if scene.scene_id in affected_ids]

    def restore(
        self, project: EditableProject, scene_id: str, history_index: int = -1
    ) -> EditableScene:
        scene = next(item for item in project.scenes if item.scene_id == scene_id)
        if not scene.history:
            raise ValueError("scene has no previous versions")
        snapshot = scene.history[history_index]
        restored = EditableScene(
            scene_id=scene.scene_id,
            duration=scene.duration,
            spec_version=snapshot["spec_version"],
            code_version=snapshot["code_version"],
            audio_version=snapshot["audio_version"],
            render_version=snapshot["render_version"],
            narration=snapshot["narration"],
            render_bytes=bytes.fromhex(snapshot["render_bytes"]),
            history=scene.history,
        )
        project.scenes = [
            restored if item.scene_id == scene_id else item for item in project.scenes
        ]
        project.assembly_inputs = [(item.scene_id, item.render_version) for item in project.scenes]
        project.rebuild_timeline()
        return restored
